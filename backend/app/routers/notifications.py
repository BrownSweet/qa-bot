"""通知：列表 / 标记已读 / 删除 / 批量已读。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..database import get_db
from ..utils import api_error

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications(
    page: int = 1, limit: int = 20, is_read: bool = None,
    db: Session = Depends(get_db), user=Depends(security.get_current_user),
):
    base = db.query(models.Notification).filter(models.Notification.user_id == user.id)
    query = base
    if is_read is not None:
        query = query.filter(models.Notification.is_read == is_read)

    total = query.count()
    unread = base.filter(models.Notification.is_read == False).count()  # noqa: E712
    page = max(page, 1)
    items = (
        query.order_by(models.Notification.created_at.desc())
        .offset((page - 1) * limit).limit(limit).all()
    )
    return {
        "notifications": [{
            "id": n.id, "title": n.title, "content": n.content, "type": n.type,
            "is_read": n.is_read, "created_at": n.created_at.isoformat(),
        } for n in items],
        "total": total, "unread_count": unread,
    }


@router.put("/{notification_id}")
def update_notification(notification_id: str, body: schemas.UpdateNotificationRequest,
                        db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    n = _get_owned(db, notification_id, user.id)
    if body.is_read is not None:
        n.is_read = body.is_read
    db.commit()
    return {"message": "通知状态更新成功"}


@router.delete("/{notification_id}")
def delete_notification(notification_id: str, db: Session = Depends(get_db),
                        user=Depends(security.get_current_user)):
    n = _get_owned(db, notification_id, user.id)
    db.delete(n)
    db.commit()
    return {"message": "通知删除成功"}


@router.post("/read-all")
def read_all(db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    db.query(models.Notification).filter(
        models.Notification.user_id == user.id,
        models.Notification.is_read == False,  # noqa: E712
    ).update({"is_read": True})
    db.commit()
    return {"message": "所有通知已标记为已读"}


def _get_owned(db: Session, notification_id: str, user_id: str) -> models.Notification:
    n = (
        db.query(models.Notification)
        .filter(models.Notification.id == notification_id,
                models.Notification.user_id == user_id)
        .first()
    )
    if not n:
        raise api_error(404, "not_found", "通知不存在")
    return n
