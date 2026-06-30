"""日志查询（支持过滤 + 分页）。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, security
from ..database import get_db

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
def query_logs(
    user_id: str = None, action: str = None, level: str = None,
    page: int = 1, limit: int = 20,
    db: Session = Depends(get_db), user=Depends(security.get_current_user),
):
    query = db.query(models.Log)
    if user_id:
        query = query.filter(models.Log.user_id == user_id)
    if action:
        query = query.filter(models.Log.action == action)
    if level:
        query = query.filter(models.Log.level == level)

    total = query.count()
    page = max(page, 1)
    logs = (
        query.order_by(models.Log.created_at.desc())
        .offset((page - 1) * limit).limit(limit).all()
    )
    return {
        "logs": [{
            "id": x.id, "user_id": x.user_id, "action": x.action, "module": x.module,
            "level": x.level, "message": x.message, "created_at": x.created_at.isoformat(),
        } for x in logs],
        "total": total, "page": page, "limit": limit,
    }
