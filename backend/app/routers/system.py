"""系统配置：获取 / 更新 / 测试 AI 连接。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import ai, models, schemas, security
from ..config import settings
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


@router.get("/readiness")
def get_readiness(db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    """Current-user setup state; ordinary Web users may read it without admin access."""
    cfg = _get_or_create(db)
    can_manage_ai = (settings.DESKTOP_MODE and user.id == "desktop-owner") or user.id in settings.ADMIN_USER_IDS
    return {
        "ai_ready": bool(ai.get_ai_config(db)["api_key"]),
        "model": cfg.model or settings.DEEPSEEK_MODEL,
        "db_config_count": db.query(models.DBConfig).filter(models.DBConfig.user_id == user.id).count(),
        "can_manage_ai": can_manage_ai,
    }


@router.get("/config")
def get_config(db: Session = Depends(get_db), user=Depends(security.get_admin_user)):
    cfg = _get_or_create(db)
    # 不回传明文 api_key，只告知是否已配置
    return {"config": {
        "id": cfg.id,
        "api_url": cfg.api_url,
        "model": cfg.model or settings.DEEPSEEK_MODEL,
        "timeout": cfg.timeout,
        "api_key_set": bool(cfg.api_key),
        "created_at": cfg.created_at.isoformat(),
        "updated_at": cfg.updated_at.isoformat(),
    }}


@router.put("/config")
def update_config(body: schemas.UpdateConfigRequest, db: Session = Depends(get_db),
                  user=Depends(security.get_admin_user)):
    cfg = _get_or_create(db)
    if body.api_url is not None:
        from urllib.parse import urlsplit
        from ..utils import api_error
        parsed = urlsplit(body.api_url)
        if (parsed.scheme not in ("https", "http") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise api_error(400, "bad_request", "AI 地址必须是有效的 HTTP(S) 地址，且不能包含用户名或密码")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise api_error(400, "bad_request", "远程 AI 服务必须使用 HTTPS")
        if body.api_url.rstrip("/") != cfg.api_url.rstrip("/") and not body.api_key:
            raise api_error(400, "bad_request", "更换 AI 服务地址时请同时输入该服务的 API Key")
    if body.api_key is not None and body.api_key != "":
        cfg.api_key = security.aes_encrypt(body.api_key)
    if body.api_url is not None:
        cfg.api_url = body.api_url
    if body.timeout is not None:
        cfg.timeout = body.timeout
    if body.model is not None:
        model = body.model.strip()
        if not model:
            from ..utils import api_error
            raise api_error(400, "bad_request", "模型名称不能为空")
        cfg.model = model
    db.commit()
    log_action(db, "update_system_config", "system", "更新系统配置", user_id=user.id)
    return {"message": "配置更新成功"}


@router.post("/test-ai")
async def test_ai(db: Session = Depends(get_db), user=Depends(security.get_admin_user)):
    cfg = ai.get_ai_config(db)
    success, message = await ai.test_connection(cfg)
    return {"success": success, "message": message}
