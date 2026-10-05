"""问答交互：SSE 流式问答 + 历史消息。"""
import asyncio
import json
import math
from contextlib import aclosing
from datetime import datetime
from typing import Optional

import anyio
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import ai, engine_utils, models, schemas, security
from ..database import gen_uuid, get_db
from ..file_policy import validate_data_file
from ..utils import api_error, log_action

router = APIRouter(prefix="/api/chat", tags=["chat"])

MAX_EVIDENCE_BYTES = 512 * 1024
SOURCE_IDENTITY_KEYS = ("type", "host", "port", "database", "username", "file_path")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _source_snapshot(config: models.DBConfig, file_path: Optional[str]) -> dict:
    """Keep identifying details, never database credentials, with each answer."""
    return {
        "id": config.id, "name": config.name, "type": config.type,
        "host": config.host, "port": config.port,
        "database": config.database_name, "username": config.username,
        "file_path": file_path,
    }


def _same_source_identity(saved: dict, current: dict) -> bool:
    return isinstance(saved, dict) and all(
        saved.get(key) == current.get(key) for key in SOURCE_IDENTITY_KEYS
    )


def _query_evidence(sql: str, columns: list, rows: list, metadata: dict,
                    generated_sql: Optional[str] = None) -> dict:
    """Persist a bounded JSON representation of the row prefix and any omission."""
    preview = []
    used_bytes = 0
    for row in rows:
        # JSON has no NaN/Infinity literals; keep the wire event parseable.
        safe_row = [str(value) if isinstance(value, float) and not math.isfinite(value)
                    else value for value in row]
        encoded = json.dumps(safe_row, ensure_ascii=False, default=str, allow_nan=False)
        size = len(encoded.encode("utf-8"))
        if used_bytes + size > MAX_EVIDENCE_BYTES:
            break
        preview.append(json.loads(encoded))
        used_bytes += size
    _result_json, analysis_coverage = ai.prepare_analysis_result(columns, rows, metadata)
    coverage = {
        **analysis_coverage,
        "evidence_rows": len(preview),
        "evidence_truncated": (len(preview) < len(rows)
                               or bool(metadata.get("cells_truncated"))
                               or bool(metadata.get("result_truncated"))),
    }
    return {
        "sql": sql,
        "generated_sql": generated_sql or sql,
        "columns": list(columns),
        "rows": preview,
        "coverage": coverage,
        "executed_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def _decode_json(value: Optional[str]):
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


async def _blocking(function, *args, on_cancel=None):
    """Keep database work off the loop, and never dispose a still-running worker's engine."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            try:
                result = await task
                if on_cancel is not None:
                    await asyncio.to_thread(on_cancel, result)
            except Exception:
                pass  # The caller still owns the cancellation outcome.
        raise


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

    if body.analysis_task_id:
        from ..analysis_tasks import AnalysisTask, render_question, verify_task_source
        task = (db.query(AnalysisTask)
                .filter(AnalysisTask.id == body.analysis_task_id,
                        AnalysisTask.user_id == user.id,
                        AnalysisTask.db_config_id == config.id,
                        AnalysisTask.deleted_at.is_(None))
                .first())
        if task is None:
            raise api_error(404, "not_found", "分析任务不存在或数据源不一致")
        verify_task_source(task, config)
        if body.analysis_task_parameters is None:
            raise api_error(400, "bad_request", "分析任务运行必须提供参数")
        if body.question != render_question(task, body.analysis_task_parameters):
            raise api_error(400, "bad_request", "问题与分析任务模板不一致")
        task_snapshot = {
            "id": task.id, "name": task.name,
            "question_template": task.question_template,
            "parameters": dict(body.analysis_task_parameters),
        }
    elif body.analysis_task_parameters:
        raise api_error(400, "bad_request", "普通问答不能携带分析任务参数")
    else:
        task_snapshot = None

    file_path = config.file_path
    if config.type in {"sqlite", "excel"}:
        # Recheck saved configs: they may predate the desktop file policy.
        file_path = validate_data_file(config.file_path or config.database_name, config.type)
    source = _source_snapshot(config, file_path)
    source_json = json.dumps(source, ensure_ascii=False)
    from ..semantic_context import get_context
    business_context = get_context(db, config.id, user.id)[:12000]
    candidates = (
        db.query(models.Message)
        .filter(models.Message.session_id == session.id, models.Message.user_id == user.id,
                models.Message.db_config_id == config.id,
                models.Message.status == "completed")
        .order_by(models.Message.created_at.desc()).yield_per(100)
    )
    previous = []
    for message in candidates:
        if _same_source_identity(_decode_json(message.source_snapshot_json), source):
            previous.append(message)
        if len(previous) == 6:
            break
    history = [{"role": message.role, "content": message.content}
               for message in reversed(previous)]
    ai_cfg = ai.get_ai_config(db)

    # 保存用户消息 + 创建待生成的助手消息
    question_message_id = gen_uuid()
    db.add(models.Message(
        id=question_message_id, session_id=session.id, user_id=user.id,
        role="user", content=body.question, status="completed",
        db_config_id=config.id, source_snapshot_json=source_json,
        analysis_task_id=body.analysis_task_id,
    ))
    assistant = models.Message(
        id=gen_uuid(), session_id=session.id, user_id=user.id,
        role="assistant", content="", status="pending",
        db_config_id=config.id, source_snapshot_json=source_json,
        question_message_id=question_message_id,
        analysis_task_id=body.analysis_task_id,
    )
    db.add(assistant)
    session.db_config_id = config.id
    session.updated_at = datetime.utcnow()
    db.commit()

    cfg_dict = {
        "type": config.type, "host": config.host, "port": config.port,
        "database": config.database_name, "username": config.username,
        "password": config.password, "file_path": file_path,
    }
    assistant_id = assistant.id

    async def generate():
        engine = None
        collected = ""
        outcome = "pending"
        generation_snapshot = None
        try:
            yield _sse("status", {"status": "connecting", "message": "正在连接数据库..."})
            engine = await _blocking(engine_utils.build_engine, cfg_dict,
                                     on_cancel=lambda created: created.dispose())

            yield _sse("status", {"status": "scanning", "message": "正在扫描数据表..."})
            schema = await _blocking(engine_utils.get_schema, engine)

            if not ai_cfg["api_key"]:
                raise RuntimeError("未配置 AI API Key，请在系统设置中填写")

            pending_snapshot = ai.sql_generation_snapshot(
                ai_cfg, schema, body.question, dialect=engine.dialect.name,
                history=history, business_context=business_context,
                task=task_snapshot,
            )
            await _blocking(_record_generation_snapshot, assistant_id, pending_snapshot)
            generation_snapshot = pending_snapshot
            yield _sse("status", {"status": "analyzing", "message": "正在分析数据..."})
            sql = await ai.generate_sql(ai_cfg, schema, body.question,
                                       dialect=engine.dialect.name, history=history,
                                       business_context=business_context)
            columns, rows, metadata = await _blocking(engine_utils.run_query_with_metadata, engine, sql)
            executed_sql = metadata.get("executed_sql") or sql
            coverage_metadata = {key: value for key, value in metadata.items()
                                 if key != "executed_sql"}
            evidence = _query_evidence(executed_sql, columns, rows, coverage_metadata,
                                       generated_sql=sql)
            await _blocking(_record_evidence, assistant_id, evidence)
            yield _sse("result", {"message_id": assistant_id, "source": source,
                                  "evidence": evidence, "columns": columns,
                                  **evidence["coverage"]})
            coverage = evidence["coverage"]
            if coverage["analysis_truncated"]:
                reasons = []
                if len(rows) > coverage["analyzed_rows"] or coverage.get("has_more"):
                    reasons.append(f"仅使用查询结果前 {coverage['analyzed_rows']} 行")
                if coverage.get("cells_truncated"):
                    reasons.append(f"{coverage['truncated_cell_count']} 个单元格内容已截断")
                if coverage.get("result_truncated"):
                    reasons.append("后续行达到结果字节上限")
                if coverage.get("analysis_bytes_truncated"):
                    reasons.append("AI 输入达到大小上限")
                notice = f"> 本次分析{'；'.join(reasons)}，不能据此推断完整结果。请缩小查询范围。\n\n"
                collected += notice
                yield _sse("message", {"content": notice})

            if (rows and coverage["analyzed_rows"] == 0) or (
                    not rows and coverage.get("result_truncated")):
                raise ValueError("单行查询结果过宽，无法安全分析，请减少所选字段后重试")

            async with aclosing(ai.analyze_stream(ai_cfg, body.question, executed_sql, columns, rows,
                                                  metadata=coverage_metadata, history=history)) as stream:
                async for chunk in stream:
                    collected += chunk
                    yield _sse("message", {"content": chunk})

            collected = collected or "（无内容）"
            outcome = "completed"
        except (asyncio.CancelledError, GeneratorExit):
            outcome = "cancelled"
            raise
        except Exception as e:  # noqa: BLE001
            msg = _friendly_error(e)
            outcome = "error"
            suffix = f"\n\n{msg}" if collected else msg
            collected += suffix
            yield _sse("status", {"status": "error", "message": msg})
            yield _sse("message", {"content": suffix})
        finally:
            if outcome == "pending":
                outcome = "cancelled"
            with anyio.CancelScope(shield=True):
                try:
                    await asyncio.to_thread(_finish, assistant_id, collected, outcome)
                finally:
                    if engine is not None:
                        await asyncio.to_thread(engine.dispose)
        yield _sse("complete", {"message_id": assistant_id, "status": outcome,
                                "question_message_id": question_message_id,
                                "db_config_id": config.id, "source": source,
                                "analysis_task_id": body.analysis_task_id,
                                "generation_snapshot": generation_snapshot})

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


def _record_evidence(message_id: str, evidence: dict):
    """Commit query facts before asking AI to interpret them."""
    from ..database import SessionLocal
    db = SessionLocal()
    try:
        msg = db.query(models.Message).filter(models.Message.id == message_id).first()
        if msg is None:
            raise RuntimeError("会话已被删除，无法保存查询依据")
        msg.evidence_json = json.dumps(evidence, ensure_ascii=False)
        db.commit()
    finally:
        db.close()


def _record_generation_snapshot(message_id: str, snapshot: dict):
    """Commit the exact SQL inputs before contacting the provider."""
    from ..database import SessionLocal
    db = SessionLocal()
    try:
        msg = db.query(models.Message).filter(models.Message.id == message_id).first()
        if msg is None:
            raise RuntimeError("会话已被删除，无法保存生成输入")
        msg.generation_snapshot_json = json.dumps(snapshot, ensure_ascii=False)
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
        "db_config_id": m.db_config_id,
        "source": _decode_json(m.source_snapshot_json),
        "question_message_id": m.question_message_id,
        "analysis_task_id": m.analysis_task_id,
        "evidence": _decode_json(m.evidence_json),
        "generation_snapshot": ai.visible_generation_snapshot(_decode_json(m.generation_snapshot_json)),
    } for m in messages]}
