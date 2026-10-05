from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models, schemas, security
from app.config import settings
from app.database import Base
from app.file_policy import validate_data_file
from app.routers import auth, system


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_password_change_revokes_existing_token(database):
    user = models.User(id="test-user", username="tester", phone="13800000000", password_hash="old-hash")
    database.add(user)
    database.commit()
    token, _ = security.create_token(user)
    credential = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert security.get_current_user(credential, database).id == user.id
    user.password_hash = "new-hash"
    database.commit()
    with pytest.raises(HTTPException) as error:
        security.get_current_user(credential, database)
    assert error.value.status_code == 401


def test_global_config_requires_admin_or_local_owner(monkeypatch):
    monkeypatch.setattr(settings, "DESKTOP_MODE", False)
    monkeypatch.setattr(settings, "ADMIN_USER_IDS", ())
    with pytest.raises(HTTPException) as error:
        security.get_admin_user(SimpleNamespace(id="ordinary-user"))
    assert error.value.status_code == 403
    monkeypatch.setattr(settings, "DESKTOP_MODE", True)
    assert security.get_admin_user(SimpleNamespace(id="desktop-owner")).id == "desktop-owner"


def test_code_echo_is_disabled_by_default(monkeypatch, database):
    monkeypatch.setattr(settings, "DEV_AUTH", False)
    with pytest.raises(HTTPException) as error:
        auth.send_code(schemas.SendCodeRequest(phone="13800000000", type="forgot"), database)
    assert error.value.status_code == 503
    assert database.query(models.VerificationCode).count() == 0


def test_changing_ai_host_does_not_forward_existing_key(database):
    database.add(models.SystemConfig(api_url="https://original.example", api_key=security.aes_encrypt("synthetic-key")))
    database.commit()
    with pytest.raises(HTTPException) as error:
        system.update_config(schemas.UpdateConfigRequest(api_url="https://another.example"), database, SimpleNamespace(id="desktop-owner"))
    assert error.value.status_code == 400
    assert database.query(models.SystemConfig).first().api_url == "https://original.example"


def test_local_source_cannot_read_application_database_or_symlink(monkeypatch, tmp_path):
    internal = tmp_path / "data"
    internal.mkdir()
    db = internal / "qabot.db"
    db.write_bytes(b"SQLite format 3\x00")
    monkeypatch.setattr(settings, "ALLOW_LOCAL_FILES", True)
    monkeypatch.setattr(settings, "DATA_DIR", internal)
    for path in (db, internal / "secrets.json"):
        with pytest.raises(HTTPException) as error:
            validate_data_file(str(path), "sqlite")
        assert error.value.status_code == 403
    link = tmp_path / "alias.sqlite"
    try:
        link.symlink_to(db)
    except OSError:
        pytest.skip("symlink creation requires privileges on this host")
    with pytest.raises(HTTPException) as error:
        validate_data_file(str(link), "sqlite")
    assert error.value.status_code == 403


def test_server_files_require_explicit_capability(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ALLOW_LOCAL_FILES", False)
    with pytest.raises(HTTPException) as error:
        validate_data_file(str(tmp_path / "data.xlsx"), "excel")
    assert error.value.status_code == 403
