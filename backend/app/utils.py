"""通用工具：统一错误、日志记录。"""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import models
from .database import gen_uuid


def api_error(status_code: int, error: str, message: str) -> HTTPException:
    """返回符合文档约定的错误结构 {error, message}。"""
    return HTTPException(status_code=status_code, detail={"error": error, "message": message})


def log_action(
    db: Session,
    action: str,
    module: str,
    message: str,
    level: str = "INFO",
    user_id: Optional[str] = None,
    details: Optional[str] = None,
):
    """写一条操作/系统日志。失败时静默（日志不应影响主流程）。"""
    try:
        entry = models.Log(
            id=gen_uuid(),
            user_id=user_id,
            action=action,
            module=module,
            level=level,
            message=message[:500],
            details=details,
        )
        db.add(entry)
        db.commit()
    except Exception:
        db.rollback()
