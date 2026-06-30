"""问答交互：SSE 流式问答 + 历史消息。"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import ai, engine_utils, models, schemas, security
from ..database import gen_uuid, get_db
from ..utils import api_error, log_action

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/send")
def send_message(body: schemas.SendMessageRequest, db: Session = Depends(get_db),
                 user=Depends(security.get_current_user)):
    session = (
        db.query(models.ChatSession)
        .filter(models.ChatSession.id == body.session_id, models.ChatSession.user_id == user.id)
        .first()
    )
    if not session:
        raise api_error(404, "not_found", "会话不存在")
    config = (
        db.query(models.DBConfig)
        .filter(models.DBConfig.id == body.db_config_id, models.DBConfig.user_id == user.id)
        .first()
    )
    if not config:
        raise api_error(404, "not_found", "数据库配置不存在")

    # 保存用户消息 + 创建待生成的助手消息
    db.add(models.Message(
        id=gen_uuid(), session_id=session.id, user_id=user.id,
        role="user", content=body.question, status="completed",
    ))
    assistant = models.Message(
        id=gen_uuid(), session_id=session.id, user_id=user.id,
        role="assistant", content="", status="pending",
    )
    db.add(assistant)
    session.updated_at = datetime.utcnow()
    db.commit()

    cfg_dict = {
        "type": config.type, "host": config.host, "port": config.port,
        "database": config.database_name, "username": config.username,
        "password": config.password, "file_path": config.file_path,
    }
    ai_cfg = ai.get_ai_config(db)
    assistant_id = assistant.id

    async def generate():
        engine = None
        collected = ""
        try:
            yield _sse("status", {"status": "connecting", "message": "正在连接数据库..."})
            engine = engine_utils.build_engine(cfg_dict)

            yield _sse("status", {"status": "scanning", "message": "正在扫描数据表..."})
            schema = engine_utils.get_schema(engine)

            if not ai_cfg["api_key"]:
                raise RuntimeError("未配置 DeepSeek API Key，请在系统设置中填写")

            yield _sse("status", {"status": "analyzing", "message": "正在分析数据..."})
            sql = await ai.generate_sql(ai_cfg, schema, body.question)
            columns, rows = engine_utils.run_query(engine, sql)

            async for chunk in ai.analyze_stream(ai_cfg, body.question, sql, columns, rows):
                collected += chunk
                yield _sse("message", {"content": chunk})

            _finish(assistant_id, collected or "（无内容）", "completed")
            yield _sse("complete", {"message_id": assistant_id, "status": "completed"})
        except Exception as e:  # noqa: BLE001
            msg = _friendly_error(e)
            yield _sse("status", {"status": "error", "message": msg})
            yield _sse("message", {"content": msg})
            _finish(assistant_id, msg, "error")
            yield _sse("complete", {"message_id": assistant_id, "status": "error"})
        finally:
            if engine is not None:
                engine.dispose()

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(generate(), media_type="text/event-stream", headers=headers)


def _finish(message_id: str, content: str, status: str):
    """流结束后用独立 session 更新助手消息（避免依赖关闭问题）。"""
    from ..database import SessionLocal
    db = SessionLocal()
    try:
        msg = db.query(models.Message).filter(models.Message.id == message_id).first()
        if msg:
            msg.content = content
            msg.status = status
            db.commit()
    finally:
        db.close()


def _friendly_error(e: Exception) -> str:
    text = str(e)
    if "api key" in text.lower() or "DeepSeek" in text:
        return "AI服务不可用，请检查系统配置中的 API Key"
    if "连接" in text or "connect" in text.lower() or "Can't connect" in text:
        return "数据库连接失败，请检查配置"
    return f"处理出错：{text}"


@router.get("/messages/{session_id}")
def get_messages(session_id: str, db: Session = Depends(get_db),
                 user=Depends(security.get_current_user)):
    session = (
        db.query(models.ChatSession)
        .filter(models.ChatSession.id == session_id, models.ChatSession.user_id == user.id)
        .first()
    )
    if not session:
        raise api_error(404, "not_found", "会话不存在")
    messages = (
        db.query(models.Message)
        .filter(models.Message.session_id == session_id)
        .order_by(models.Message.created_at.asc())
        .all()
    )
    return {"messages": [{
        "id": m.id, "role": m.role, "content": m.content,
        "status": m.status, "created_at": m.created_at.isoformat(),
    } for m in messages]}
