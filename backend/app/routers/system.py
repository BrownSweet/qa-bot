"""系统配置：获取 / 更新 / 测试 AI 连接。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import ai, models, schemas, security
from ..database import gen_uuid, get_db
from ..utils import log_action

router = APIRouter(prefix="/api/system", tags=["system"])


def _get_or_create(db: Session) -> models.SystemConfig:
    cfg = db.query(models.SystemConfig).first()
    if not cfg:
        cfg = models.SystemConfig(id=gen_uuid(), api_key="", api_url="https://api.deepseek.com", timeout=30)
        db.add(cfg)
        db.commit()
    return cfg


@router.get("/config")
def get_config(db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    cfg = _get_or_create(db)
    # 不回传明文 api_key，只告知是否已配置
    return {"config": {
        "id": cfg.id,
        "api_url": cfg.api_url,
        "timeout": cfg.timeout,
        "api_key_set": bool(cfg.api_key),
        "created_at": cfg.created_at.isoformat(),
        "updated_at": cfg.updated_at.isoformat(),
    }}


@router.put("/config")
def update_config(body: schemas.UpdateConfigRequest, db: Session = Depends(get_db),
                  user=Depends(security.get_current_user)):
    cfg = _get_or_create(db)
    if body.api_key is not None and body.api_key != "":
        cfg.api_key = security.aes_encrypt(body.api_key)
    if body.api_url is not None:
        cfg.api_url = body.api_url
    if body.timeout is not None:
        cfg.timeout = body.timeout
    db.commit()
    log_action(db, "update_system_config", "system", "更新系统配置", user_id=user.id)
    return {"message": "配置更新成功"}


@router.post("/test-ai")
async def test_ai(db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    cfg = ai.get_ai_config(db)
    success, message = await ai.test_connection(cfg)
    return {"success": success, "message": message}
