"""会话管理：列表(可搜索) / 创建 / 更新(改名、置顶) / 删除。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..database import gen_uuid, get_db
from ..utils import api_error, log_action

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("")
def list_sessions(keyword: str = None, db: Session = Depends(get_db),
                  user=Depends(security.get_current_user)):
    query = db.query(models.ChatSession).filter(models.ChatSession.user_id == user.id)
    if keyword:
        query = query.filter(models.ChatSession.name.like(f"%{keyword}%"))
    sessions = query.order_by(
        models.ChatSession.is_pinned.desc(),
        models.ChatSession.updated_at.desc(),
    ).all()
    return {"sessions": [{
        "id": s.id, "name": s.name, "is_pinned": s.is_pinned,
        "created_at": s.created_at.isoformat(), "updated_at": s.updated_at.isoformat(),
    } for s in sessions]}


@router.post("", status_code=201)
def create_session(body: schemas.CreateSessionRequest, db: Session = Depends(get_db),
                   user=Depends(security.get_current_user)):
    session = models.ChatSession(
        id=gen_uuid(), user_id=user.id, name=(body.name or "新会话"), is_pinned=False,
    )
    db.add(session)
    db.commit()
    log_action(db, "create_session", "session", "创建会话", user_id=user.id)
    return {
        "message": "会话创建成功",
        "session": {"id": session.id, "name": session.name, "is_pinned": session.is_pinned,
                    "created_at": session.created_at.isoformat()},
    }


@router.put("/{session_id}")
def update_session(session_id: str, body: schemas.UpdateSessionRequest,
                   db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    session = _get_owned(db, session_id, user.id)
    if body.name is not None:
        session.name = body.name
    if body.is_pinned is not None:
        session.is_pinned = body.is_pinned
    db.commit()
    return {"message": "会话更新成功"}


@router.delete("/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db),
                   user=Depends(security.get_current_user)):
    session = _get_owned(db, session_id, user.id)
    # 级联删除消息
    db.query(models.Message).filter(models.Message.session_id == session_id).delete()
    db.delete(session)
    db.commit()
    log_action(db, "delete_session", "session", "删除会话", user_id=user.id)
    return {"message": "会话删除成功"}


def _get_owned(db: Session, session_id: str, user_id: str) -> models.ChatSession:
    session = (
        db.query(models.ChatSession)
        .filter(models.ChatSession.id == session_id, models.ChatSession.user_id == user_id)
        .first()
    )
    if not session:
        raise api_error(404, "not_found", "会话不存在")
    return session
