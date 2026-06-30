"""数据库配置：列表 / 添加 / 删除 / 测试连接。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import engine_utils, models, schemas, security
from ..database import gen_uuid, get_db
from ..utils import api_error, log_action

router = APIRouter(prefix="/api/db", tags=["db"])


def _serialize(c: models.DBConfig) -> dict:
    return {
        "id": c.id, "name": c.name, "type": c.type, "host": c.host, "port": c.port,
        "database": c.database_name, "username": c.username, "file_path": c.file_path,
        "created_at": c.created_at.isoformat(),
    }


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


@router.delete("/configs/{config_id}")
def delete_config(config_id: str, db: Session = Depends(get_db),
                  user=Depends(security.get_current_user)):
    config = (
        db.query(models.DBConfig)
        .filter(models.DBConfig.id == config_id, models.DBConfig.user_id == user.id)
        .first()
    )
    if not config:
        raise api_error(404, "not_found", "配置不存在")
    db.delete(config)
    db.commit()
    log_action(db, "delete_db_config", "db", f"删除数据库配置: {config.name}", user_id=user.id)
    return {"message": "配置删除成功"}


@router.post("/test-connection")
def test_connection(body: schemas.TestConnectionRequest, db: Session = Depends(get_db),
                    user=Depends(security.get_current_user)):
    cfg = body.model_dump()
    # 明文密码 -> 加密，复用 build_engine 的解密逻辑
    cfg["password"] = security.aes_encrypt(cfg.get("password")) if cfg.get("password") else None
    try:
        engine = engine_utils.build_engine(cfg)
        engine_utils.get_schema(engine)
        engine.dispose()
        return {"success": True, "message": "连接成功"}
    except Exception as e:
        return {"success": False, "message": f"连接失败：{e}"}
