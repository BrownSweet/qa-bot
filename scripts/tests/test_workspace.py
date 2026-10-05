"""Real SQLite/ZIP recovery tests; every file and secret is synthetic and temporary."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app import workspace


def encrypt(value, key="a" * 64):
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    iv = b"0" * 16
    padder = padding.PKCS7(128).padder()
    padded = padder.update(value.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(hashlib.sha256(key.encode()).digest()), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + cipher.update(padded) + cipher.finalize()).decode()


@pytest.fixture
def data(tmp_path):
    folder = tmp_path / "profile" / "data"
    folder.mkdir(parents=True)
    (folder / "secrets.json").write_text(json.dumps({"jwt": "j" * 64, "aes": "a" * 64}))
    with workspace._sqlite(folder / "qabot.db") as connection:
        connection.executescript("""
            CREATE TABLE users (id TEXT PRIMARY KEY);
            CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE messages (id TEXT PRIMARY KEY, content TEXT);
            CREATE TABLE system_configs (id TEXT PRIMARY KEY, api_key TEXT);
            CREATE TABLE db_configs (id TEXT PRIMARY KEY, type TEXT, file_path TEXT, password TEXT);
            INSERT INTO users VALUES ('synthetic');
            INSERT INTO sessions VALUES ('session', '保留的旧会话');
            INSERT INTO messages VALUES ('message', '原有消息');
        """)
        connection.execute("INSERT INTO system_configs VALUES ('config', ?)", (encrypt("synthetic-api-key"),))
    return folder


def test_backup_restore_preserves_data_keys_and_previous_snapshot(data, tmp_path):
    archive = tmp_path / "backup.zip"
    result = workspace.create_backup(data, archive)
    assert result["valid"] and result["schemaVersion"] == 1
    assert workspace.inspect_backup(archive)["valid"]
    with workspace._sqlite(data / "qabot.db") as connection:
        connection.execute("UPDATE sessions SET name = '变更后的会话'")
    restored = workspace.restore_backup(data, archive)
    assert Path(restored["previousBackup"]).is_file()
    with workspace._sqlite(data / "qabot.db") as connection:
        assert connection.execute("SELECT name FROM sessions").fetchone()[0] == "保留的旧会话"
    assert workspace.validate_database(data / "qabot.db", data / "secrets.json")["schemaVersion"] == 1


def test_backup_is_online_consistent_while_sqlite_is_open(data, tmp_path):
    with workspace._sqlite(data / "qabot.db") as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("UPDATE messages SET content='已提交' ")
        connection.commit()
        workspace.create_backup(data, tmp_path / "online.zip")
    assert workspace.inspect_backup(tmp_path / "online.zip")["valid"]


def test_missing_or_wrong_keys_do_not_create_a_false_valid_backup(data, tmp_path):
    (data / "secrets.json").write_text(json.dumps({"jwt": "j" * 64, "aes": "b" * 64}))
    with pytest.raises(RuntimeError, match="不匹配"):
        workspace.create_backup(data, tmp_path / "invalid.zip")
    (data / "secrets.json").unlink()
    with pytest.raises(RuntimeError, match="缺失"):
        workspace.create_backup(data, tmp_path / "invalid.zip")


def test_existing_database_without_key_blocks_startup_without_regenerating(data):
    (data / "secrets.json").unlink()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "QA_DESKTOP_MODE": "1", "QA_DATA_DIR": str(data), "QA_DESKTOP_TOKEN": "t" * 64}
    result = subprocess.run([sys.executable, "-B", "-c", "from app.config import settings"], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and "密钥缺失" in result.stderr
    assert not (data / "secrets.json").exists()


def test_desktop_ai_defaults_ignore_parent_environment_but_web_preserves_it(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "QA_DESKTOP_MODE": "1", "QA_DATA_DIR": str(tmp_path / "data"),
           "QA_DESKTOP_TOKEN": "t" * 64, "DEEPSEEK_API_URL": "https://synthetic.invalid", "DEEPSEEK_MODEL": "environment-model", "DEEPSEEK_API_KEY": "synthetic-key"}
    code = "from app.config import settings; import json; print(json.dumps([settings.DEEPSEEK_API_URL, settings.DEEPSEEK_MODEL, bool(settings.DEEPSEEK_API_KEY)]))"
    desktop = subprocess.run([sys.executable, "-B", "-c", code], env=env, capture_output=True, text=True, check=True)
    assert json.loads(desktop.stdout) == ["https://api.deepseek.com", "deepseek-chat", False]
    web = subprocess.run([sys.executable, "-B", "-c", code], env={**env, "QA_DESKTOP_MODE": "0"}, capture_output=True, text=True, check=True)
    assert json.loads(web.stdout) == ["https://synthetic.invalid", "environment-model", True]


def test_tampered_archive_is_rejected_without_changing_current_data(data, tmp_path):
    archive = tmp_path / "valid.zip"
    workspace.create_backup(data, archive)
    invalid = tmp_path / "invalid.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(invalid, "w") as target:
        for name in source.namelist():
            target.writestr(name, b"modified" if name == "qabot.db" else source.read(name))
    before = (data / "qabot.db").read_bytes()
    with pytest.raises(RuntimeError, match="校验失败"):
        workspace.restore_backup(data, invalid)
    assert (data / "qabot.db").read_bytes() == before


def test_archive_path_traversal_is_rejected(data, tmp_path):
    archive = tmp_path / "unsafe.zip"
    workspace.create_backup(data, archive)
    with zipfile.ZipFile(archive, "a") as target:
        target.writestr("../escaped", "bad")
    with pytest.raises(RuntimeError, match="清单"):
        workspace.inspect_backup(archive)
    assert not (tmp_path / "escaped").exists()


def test_future_schema_cannot_be_restored(data, tmp_path):
    with workspace._sqlite(data / "qabot.db") as connection:
        connection.executescript("CREATE TABLE schema_migrations(version INTEGER); INSERT INTO schema_migrations VALUES (999)")
    with pytest.raises(RuntimeError, match="更新版本"):
        workspace.create_backup(data, tmp_path / "future.zip")


def test_interrupted_pair_replacement_recovers_originals(data):
    old_db = (data / "qabot.db").read_bytes()
    old_keys = (data / "secrets.json").read_bytes()
    journal = data / ".restore-rollback"
    journal.mkdir()
    (journal / "qabot.db").write_bytes(old_db)
    (journal / "secrets.json").write_bytes(old_keys)
    orphan = data.parent / "sources" / ("restored-" + "a" * 32)
    orphan.mkdir(parents=True)
    (orphan / "synthetic.sqlite").write_bytes(b"partial copied source")
    (journal / "state.json").write_text(json.dumps({"originals": ["qabot.db", "secrets.json"], "relocatedSources": orphan.name}))
    (data / "qabot.db").write_bytes(b"partially replaced")
    workspace.recover_interrupted_restore(data)
    assert (data / "qabot.db").read_bytes() == old_db
    assert (data / "secrets.json").read_bytes() == old_keys
    assert not journal.exists()
    assert not orphan.exists()


def test_completed_restore_cleanup_never_rolls_back_new_data(data):
    completed = data / (".restore-completed-" + "b" * 32)
    completed.mkdir()
    (completed / "state.json").write_text(json.dumps({"originals": ["qabot.db", "secrets.json"]}))
    # Simulate a crash after commit while deleting the obsolete originals.
    (completed / "secrets.json").write_text("obsolete")
    current = (data / "qabot.db").read_bytes()
    workspace.recover_interrupted_restore(data)
    assert (data / "qabot.db").read_bytes() == current
    assert not completed.exists()


def test_sqlite_context_closes_handles_even_after_failure(tmp_path):
    with pytest.raises(RuntimeError):
        with workspace._sqlite(tmp_path / "closed.sqlite") as connection:
            raise RuntimeError("synthetic interruption")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_portable_backup_carries_and_relocates_external_sqlite(data, tmp_path):
    source = tmp_path / "external.sqlite"
    with workspace._sqlite(source) as connection:
        connection.executescript("CREATE TABLE sales(amount INTEGER); INSERT INTO sales VALUES (42)")
    with workspace._sqlite(data / "qabot.db") as connection:
        connection.execute("INSERT INTO db_configs VALUES ('source', 'sqlite', ?, NULL)", (str(source),))
    archive = tmp_path / "portable.zip"
    workspace.create_backup(data, archive, include_sources=True)
    source.unlink()
    destination = tmp_path / "other-machine" / "data"
    workspace.restore_backup(destination, archive)
    with workspace._sqlite(destination / "qabot.db") as connection:
        relocated = Path(connection.execute("SELECT file_path FROM db_configs").fetchone()[0])
    assert relocated.is_file() and not relocated.is_relative_to(destination)
    with workspace._sqlite(relocated) as connection:
        assert connection.execute("SELECT amount FROM sales").fetchone()[0] == 42


def test_config_only_backup_reports_missing_sources(data, tmp_path):
    with workspace._sqlite(data / "qabot.db") as connection:
        connection.execute("INSERT INTO db_configs VALUES ('missing', 'excel', ?, NULL)", (str(tmp_path / "gone.xlsx"),))
    archive = tmp_path / "references.zip"
    workspace.create_backup(data, archive)
    assert workspace.inspect_backup(archive)["missingExternalFileIds"] == ["missing"]


def test_legacy_database_name_path_is_carried_and_relocated(data, tmp_path):
    source = tmp_path / "legacy.sqlite"
    with workspace._sqlite(source) as connection:
        connection.executescript("CREATE TABLE legacy(value TEXT); INSERT INTO legacy VALUES ('retained')")
    with workspace._sqlite(data / "qabot.db") as connection:
        connection.execute("ALTER TABLE db_configs ADD COLUMN database_name TEXT")
        connection.execute("INSERT INTO db_configs VALUES ('old-source', 'sqlite', NULL, NULL, ?)", (str(source),))
    archive = tmp_path / "legacy.zip"
    workspace.create_backup(data, archive, include_sources=True)
    source.unlink()
    destination = tmp_path / "another-machine" / "data"
    workspace.restore_backup(destination, archive)
    with workspace._sqlite(destination / "qabot.db") as connection:
        relocated = connection.execute("SELECT file_path FROM db_configs WHERE id='old-source'").fetchone()[0]
    with workspace._sqlite(relocated) as connection:
        assert connection.execute("SELECT value FROM legacy").fetchone()[0] == "retained"


def test_source_that_grows_during_snapshot_is_rejected_before_publishing(data, tmp_path, monkeypatch):
    source = tmp_path / "growing.xlsx"
    source.write_bytes(b"small original")
    with workspace._sqlite(data / "qabot.db") as connection:
        connection.execute("INSERT INTO db_configs VALUES ('growing', 'excel', ?, NULL)", (str(source),))
    monkeypatch.setattr(workspace, "MAX_SOURCE_BYTES", 1024)
    original_copy = workspace.shutil.copyfile
    def growing_copy(original, destination):
        result = original_copy(original, destination)
        if Path(original) == source:
            Path(destination).write_bytes(b"x" * 1025)
        return result
    monkeypatch.setattr(workspace.shutil, "copyfile", growing_copy)
    archive = tmp_path / "existing-backup.zip"
    archive.write_bytes(b"previous backup")
    with pytest.raises(RuntimeError, match="备份后的单个数据文件"):
        workspace.create_backup(data, archive, include_sources=True)
    assert archive.read_bytes() == b"previous backup"


def test_diagnostics_excludes_database_keys_and_redacts_credentials(data, tmp_path):
    logs = data.parent / "logs"
    logs.mkdir()
    (logs / "desktop.log").write_text("Authorization: Bearer secret-token\napi_key=sk-synthetic-key\nmysql://user:password@host/database\nSQL: SELECT salary FROM employees; result=[{name: Alice, salary: 100}]\n")
    output = tmp_path / "diagnostics.zip"
    workspace.export_diagnostics(data, output)
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"diagnostics.json"}
        content = archive.read("diagnostics.json").decode()
        assert "secret-token" not in content and "sk-synthetic-key" not in content and "user:password" not in content
        assert "salary" not in content and "Alice" not in content
        assert json.loads(content)["logSummary"][0]["sampledLines"] == 4


def test_legacy_schema_migrates_nullable_provenance_and_keeps_history(data):
    from sqlalchemy import create_engine, inspect, text
    from app.database import prepare_schema
    engine = create_engine("sqlite:///" + str(data / "qabot.db"))
    config = SimpleNamespace(DESKTOP_MODE=True, DATA_DIR=data, VERSION="1.2.1")
    prepare_schema(engine, config)
    expected = {"db_config_id", "source_snapshot_json", "question_message_id", "evidence_json",
                "analysis_task_id", "generation_snapshot_json"}
    assert expected.issubset({column["name"] for column in inspect(engine).get_columns("messages")})
    with engine.connect() as connection:
        row = connection.execute(text("SELECT content, evidence_json, db_config_id FROM messages")).one()
        assert tuple(row) == ("原有消息", None, None)
        assert connection.execute(text("SELECT model FROM system_configs")).scalar() == "deepseek-chat"
        assert connection.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar() == workspace.CURRENT_SCHEMA_VERSION
    backups = list((data / "backups").glob("pre-upgrade-*.zip"))
    assert len(backups) == 1 and workspace.inspect_backup(backups[0])["schemaVersion"] == 1
    prepare_schema(engine, config)
    assert len(list((data / "backups").glob("pre-upgrade-*.zip"))) == 1
    engine.dispose()


def test_v2_tasks_and_semantics_gain_unknown_identity_without_fabrication(data):
    from sqlalchemy import create_engine, text
    from app.database import prepare_schema
    with workspace._sqlite(data / "qabot.db") as connection:
        connection.executescript("""
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT);
            INSERT INTO schema_migrations VALUES (2, 'synthetic');
            CREATE TABLE source_semantics(id TEXT PRIMARY KEY);
            CREATE TABLE analysis_tasks(id TEXT PRIMARY KEY);
            INSERT INTO source_semantics VALUES ('old-rule');
            INSERT INTO analysis_tasks VALUES ('old-task');
        """)
    engine = create_engine("sqlite:///" + str(data / "qabot.db"))
    prepare_schema(engine, SimpleNamespace(DESKTOP_MODE=False))
    with engine.connect() as connection:
        assert connection.execute(text("SELECT source_identity_json FROM source_semantics")).scalar() is None
        assert connection.execute(text("SELECT source_identity_json FROM analysis_tasks")).scalar() is None
        assert connection.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar() == workspace.CURRENT_SCHEMA_VERSION
    engine.dispose()


def test_v3_evaluation_runs_gain_nullable_case_snapshot(data):
    from sqlalchemy import create_engine, text
    from app.database import prepare_schema
    with workspace._sqlite(data / "qabot.db") as connection:
        connection.executescript("""
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT);
            INSERT INTO schema_migrations VALUES (3, 'synthetic');
            CREATE TABLE evaluation_runs(id TEXT PRIMARY KEY, passed INTEGER);
            INSERT INTO evaluation_runs VALUES ('old-result', 1);
        """)
    engine = create_engine("sqlite:///" + str(data / "qabot.db"))
    prepare_schema(engine, SimpleNamespace(DESKTOP_MODE=False))
    with engine.connect() as connection:
        assert tuple(connection.execute(text("SELECT passed, case_snapshot_json, generation_snapshot_json FROM evaluation_runs")).one()) == (1, None, None)
        assert connection.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar() == workspace.CURRENT_SCHEMA_VERSION
    engine.dispose()
