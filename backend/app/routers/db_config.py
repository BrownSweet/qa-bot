"""数据库配置、连接维护以及只读结构预览。"""
import asyncio
from fastapi import APIRouter, Depends, Query
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from .. import engine_utils, models, schemas, security
from ..database import gen_uuid, get_db
from ..utils import api_error, log_action
from ..file_policy import validate_data_file

router = APIRouter(prefix="/api/db", tags=["db"])
PROBE_TIMEOUT_SECONDS = 30


def _serialize(c: models.DBConfig) -> dict:
    return {
        "id": c.id, "name": c.name, "type": c.type, "host": c.host, "port": c.port,
        "database": c.database_name, "username": c.username, "file_path": c.file_path,
        "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat(),
    }


def _owned_config(db: Session, config_id: str, user_id: str) -> models.DBConfig:
    config = db.query(models.DBConfig).filter(
        models.DBConfig.id == config_id, models.DBConfig.user_id == user_id,
    ).first()
    if config is None:
        raise api_error(404, "not_found", "配置不存在")
    return config


def _engine_config(config: models.DBConfig) -> dict:
    file_path = config.file_path
    if config.type in ("sqlite", "excel"):
        file_path = validate_data_file(file_path or config.database_name, config.type)
    return {
        "type": config.type, "host": config.host, "port": config.port,
        "database": config.database_name, "username": config.username,
        "password": config.password, "file_path": file_path,
    }


def _candidate_config(config: models.DBConfig, body: schemas.DBConfigRequest) -> dict:
    """Build a complete prospective connection without changing the saved source."""
    if body.type in ("sqlite", "excel"):
        return {"type": body.type, "host": None, "port": None, "database": None,
                "username": None, "password": None,
                "file_path": validate_data_file(body.file_path or body.database, body.type)}
    return {"type": body.type, "host": body.host, "port": body.port,
            "database": body.database, "username": body.username, "file_path": None,
            "password": (security.aes_encrypt(body.password) if body.password
                         else config.password if config.type == body.type else None)}


def _apply_candidate(config: models.DBConfig, name: str, candidate: dict) -> None:
    config.name = name.strip()
    config.type = candidate["type"]
    config.host = candidate["host"]
    config.port = candidate["port"]
    config.database_name = candidate["database"]
    config.username = candidate["username"]
    config.password = candidate["password"]
    config.file_path = candidate["file_path"]


def _probe_candidate(candidate: dict) -> None:
    engine = engine_utils.build_engine(candidate)
    try:
        engine_utils.get_schema(engine)
    finally:
        engine.dispose()


@router.get("/configs")
def list_configs(db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    configs = (
        db.query(models.DBConfig)
        .filter(models.DBConfig.user_id == user.id)
        .order_by(models.DBConfig.created_at.desc())
        .all()
    )
    return {"configs": [_serialize(c) for c in configs]}


@router.post("/configs", status_code=201)
def add_config(body: schemas.DBConfigRequest, db: Session = Depends(get_db),
               user=Depends(security.get_current_user)):
    if not body.name.strip():
        raise api_error(400, "bad_request", "配置名称不能为空")
    if body.type in ("sqlite", "excel"):
        body.file_path = validate_data_file(body.file_path or body.database, body.type)

    config = models.DBConfig(
        id=gen_uuid(), user_id=user.id, name=body.name, type=body.type,
        host=body.host, port=body.port, database_name=body.database,
        username=body.username,
        password=security.aes_encrypt(body.password) if body.password else None,
        file_path=body.file_path,
    )
    db.add(config)
    db.commit()
    log_action(db, "add_db_config", "db", f"添加数据库配置: {body.name}", user_id=user.id)
    return {
        "message": "配置添加成功",
        "config": {"id": config.id, "name": config.name, "type": config.type,
                   "created_at": config.created_at.isoformat()},
    }


@router.put("/configs/{config_id}")
def update_config(config_id: str, body: schemas.DBConfigRequest,
                  db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    """Update a source in place so sessions and their saved provenance retain its id."""
    config = _owned_config(db, config_id, user.id)
    if not body.name.strip():
        raise api_error(400, "bad_request", "配置名称不能为空")
    _apply_candidate(config, body.name, _candidate_config(config, body))
    db.commit()
    log_action(db, "update_db_config", "db", f"更新数据库配置: {config.name}", user_id=user.id)
    return {"message": "配置更新成功", "config": _serialize(config)}


@router.post("/configs/{config_id}/test-update")
async def test_and_update_config(config_id: str, body: schemas.DBConfigRequest,
                                 db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    """Test the prospective connection before making any saved-source change."""
    config = _owned_config(db, config_id, user.id)
    if not body.name.strip():
        raise api_error(400, "bad_request", "配置名称不能为空")
    candidate = _candidate_config(config, body)
    try:
        await asyncio.wait_for(asyncio.to_thread(_probe_candidate, candidate), PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        return {"success": False, "message": f"连接测试超过 {PROBE_TIMEOUT_SECONDS} 秒，原配置未修改"}
    except Exception as error:
        return {"success": False, "message": f"连接失败：{error}；原配置未修改"}
    _apply_candidate(config, body.name, candidate)
    db.commit()
    log_action(db, "update_db_config", "db", f"更新数据库配置: {config.name}", user_id=user.id)
    return {"success": True, "message": "连接成功，配置已保存", "config": _serialize(config)}


@router.delete("/configs/{config_id}")
def delete_config(config_id: str, db: Session = Depends(get_db),
                  user=Depends(security.get_current_user)):
    config = _owned_config(db, config_id, user.id)
    # A session may outlive its source. Keep message snapshots, but require the
    # user to select a current source before sending another question.
    db.query(models.ChatSession).filter(
        models.ChatSession.user_id == user.id,
        models.ChatSession.db_config_id == config_id,
    ).update({models.ChatSession.db_config_id: None}, synchronize_session=False)
    db.delete(config)
    db.commit()
    log_action(db, "delete_db_config", "db", f"删除数据库配置: {config.name}", user_id=user.id)
    return {"message": "配置删除成功"}


@router.post("/test-connection")
def test_connection(body: schemas.TestConnectionRequest, db: Session = Depends(get_db),
                    user=Depends(security.get_current_user)):
    cfg = body.model_dump()
    if body.type in ("sqlite", "excel"):
        cfg["file_path"] = validate_data_file(body.file_path or body.database, body.type)
    # 明文密码 -> 加密，复用 build_engine 的解密逻辑
    cfg["password"] = security.aes_encrypt(cfg.get("password")) if cfg.get("password") else None
    engine = None
    try:
        engine = engine_utils.build_engine(cfg)
        engine_utils.get_schema(engine)
        return {"success": True, "message": "连接成功"}
    except Exception as e:
        return {"success": False, "message": f"连接失败：{e}"}
    finally:
        if engine is not None:
            engine.dispose()


@router.post("/configs/{config_id}/test-connection")
def test_saved_connection(config_id: str, db: Session = Depends(get_db),
                          user=Depends(security.get_current_user)):
    config = _owned_config(db, config_id, user.id)
    engine = None
    try:
        engine = engine_utils.build_engine(_engine_config(config))
        engine_utils.get_schema(engine)
        return {"success": True, "message": "连接成功"}
    except Exception as error:
        return {"success": False, "message": f"连接失败：{error}"}
    finally:
        if engine is not None:
            engine.dispose()


@router.get("/configs/{config_id}/schema")
def get_source_schema(config_id: str, db: Session = Depends(get_db),
                      user=Depends(security.get_current_user)):
    config = _owned_config(db, config_id, user.id)
    engine = engine_utils.build_engine(_engine_config(config))
    try:
        inspector = inspect(engine)
        names = inspector.get_table_names()
        tables = [{"name": name, "columns": [
            {"name": column["name"], "type": str(column["type"])}
            for column in inspector.get_columns(name)
        ]} for name in names[:100]]
        return {"tables": tables, "table_count": len(names), "has_more": len(names) > 100}
    finally:
        engine.dispose()


@router.get("/configs/{config_id}/preview")
def preview_source_table(config_id: str, table: str = Query(min_length=1, max_length=255),
                         db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    config = _owned_config(db, config_id, user.id)
    engine = engine_utils.build_engine(_engine_config(config))
    try:
        if table not in inspect(engine).get_table_names():
            raise api_error(404, "not_found", "数据表不存在")
        quoted = engine.dialect.identifier_preparer.quote(table)
        columns, rows, metadata = engine_utils.run_query_with_metadata(
            engine, f"SELECT * FROM {quoted}", limit=10,
        )
        return {"columns": columns, "rows": rows, "metadata": metadata}
    finally:
        engine.dispose()
