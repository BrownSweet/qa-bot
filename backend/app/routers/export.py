"""数据导出：将会话消息导出为 Excel 或 CSV。"""
import csv
import io
import json
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import ai, models, schemas, security
from ..database import get_db
from ..utils import api_error, log_action

router = APIRouter(prefix="/api", tags=["export"])

_CSV_IGNORED_PREFIX = "\ufeff\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
_DANGEROUS_CSV_START = re.compile(r"^[\s\x00-\x1f\ufeff\u200b-\u200f\u202a-\u202e]*[=+\-@]")
MAX_SESSION_EXPORT_MESSAGES = 10_000
MAX_SESSION_EXPORT_BYTES = 8 * 1024 * 1024
MAX_SESSION_PREVIEW_ROWS = 10_000
EXCEL_JSON_CHUNK_CHARS = 30_000

SESSION_HEADERS = [
    "角色", "内容", "状态", "时间", "消息ID", "来源快照(JSON)",
    "执行SQL", "模型原始SQL", "查询时间", "查询范围(JSON)",
    "结果列(JSON)", "已保存结果预览(JSON或证据页)", "生成输入快照(JSON或证据页)",
]


def _export_value(value):
    return json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else value


def _csv_cell(value):
    """Keep untrusted text literal when a CSV is opened by a spreadsheet app."""
    value = _export_value(value)
    if isinstance(value, str) and (_DANGEROUS_CSV_START.match(value)
                                   or (value and value[0] in _CSV_IGNORED_PREFIX)
                                   or value.startswith(("\t", "\r", "\n"))):
        return "'" + value
    return value


def _write_excel_rows(sheet, rows):
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    truncated = 0
    for row_index, row in enumerate(rows, 1):
        for column_index, value in enumerate(row, 1):
            cell = sheet.cell(row=row_index, column=column_index)
            value = _export_value(value)
            if isinstance(value, str):
                # openpyxl recognizes a leading '=' as a formula by default.
                value = ILLEGAL_CHARACTERS_RE.sub("", value)
                if len(value) > 32767:
                    value = value[:32740] + "…（Excel 单元格已截断）"
                    truncated += 1
                cell.value = value
                cell.data_type = "s"
            else:
                cell.value = value
    return truncated


def _csv_bytes(rows) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows([_csv_cell(value) for value in row] for row in rows)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _saved_source(message: models.Message) -> dict:
    try:
        source = json.loads(message.source_snapshot_json or "")
        return source if isinstance(source, dict) else {}
    except (ValueError, TypeError):
        return {}


def _saved_evidence(message: models.Message) -> dict:
    if not message.evidence_json:
        return {}
    try:
        evidence = json.loads(message.evidence_json)
        if not isinstance(evidence, dict) or not isinstance(evidence.get("rows"), list):
            raise ValueError("invalid evidence")
        return evidence
    except (TypeError, ValueError):
        raise api_error(409, "invalid_evidence", "会话中有无法解析的查询依据，请检查本地数据")


def _saved_generation_snapshot(message: models.Message) -> dict:
    if not message.generation_snapshot_json:
        return {}
    try:
        snapshot = json.loads(message.generation_snapshot_json)
        if not isinstance(snapshot, dict):
            raise ValueError("invalid generation snapshot")
        return ai.visible_generation_snapshot(snapshot)
    except (TypeError, ValueError):
        raise api_error(409, "invalid_generation_snapshot", "会话中有无法解析的生成输入快照，请检查本地数据")


def _json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str) if value is not None else ""


def _evidence_chunks(message_id: str, kind: str, row_number, value):
    serialized = _json_text(value)
    parts = [serialized[start:start + EXCEL_JSON_CHUNK_CHARS]
             for start in range(0, len(serialized), EXCEL_JSON_CHUNK_CHARS)] or [""]
    for index, part in enumerate(parts, 1):
        yield [message_id, kind, row_number, index, len(parts), part]


@router.post("/export")
def export_session(body: schemas.ExportRequest, db: Session = Depends(get_db),
                   user=Depends(security.get_current_user)):
    session = (
        db.query(models.ChatSession)
        .filter(models.ChatSession.id == body.session_id, models.ChatSession.user_id == user.id)
        .first()
    )
    if not session:
        raise api_error(404, "not_found", "会话不存在")

    query = db.query(models.Message).filter(models.Message.session_id == session.id,
                                            models.Message.user_id == user.id)
    count = query.with_entities(func.count(models.Message.id)).scalar() or 0
    if count > MAX_SESSION_EXPORT_MESSAGES:
        raise api_error(413, "export_too_large", f"会话超过 {MAX_SESSION_EXPORT_MESSAGES} 条消息，请缩小会话后导出")

    rows = [SESSION_HEADERS]
    evidence_rows = [["消息ID", "记录类型", "结果行号", "片段序号", "片段总数", "JSON片段"]]
    raw_bytes = 0
    preview_count = 0
    for m in query.order_by(models.Message.created_at.asc()).yield_per(50):
        raw_bytes += sum(len((value or "").encode("utf-8"))
                         for value in (m.content, m.source_snapshot_json,
                                       m.evidence_json, m.generation_snapshot_json))
        if raw_bytes > MAX_SESSION_EXPORT_BYTES:
            raise api_error(413, "export_too_large", "会话导出超过 8 MiB，请缩小会话后导出")
        role = "用户" if m.role == "user" else "助手"
        source = _saved_source(m)
        evidence = _saved_evidence(m)
        generation_snapshot = _saved_generation_snapshot(m)
        preview = evidence.get("rows", [])
        preview_count += len(preview)
        if preview_count > MAX_SESSION_PREVIEW_ROWS:
            raise api_error(413, "export_too_large", "会话导出超过 10000 行结果预览，请缩小会话后导出")
        preview_json = _json_text(preview) if evidence else ""
        if body.format == "excel" and len(preview_json) > 32767:
            preview_json = f"见「查询证据」页；仅保存结果预览 {len(preview)} 行"
        generation_json = _json_text(generation_snapshot) if generation_snapshot else ""
        if body.format == "excel" and len(generation_json) > 32767:
            generation_json = "见「查询证据」页；生成输入快照按片段保存"
        rows.append([
            role, m.content, m.status, m.created_at.isoformat(), m.id,
            _json_text(source) if source else "",
            evidence.get("sql") or "", evidence.get("generated_sql") or "",
            evidence.get("executed_at") or "",
            _json_text(evidence.get("coverage")) if evidence else "",
            _json_text(evidence.get("columns")) if evidence else "",
            preview_json, generation_json,
        ])
        if body.format == "excel" and generation_snapshot:
            evidence_rows.extend(_evidence_chunks(m.id, "生成输入快照", "", generation_snapshot))
        if body.format == "excel" and evidence:
            details = {"source": source, "sql": evidence.get("sql"),
                       "generated_sql": evidence.get("generated_sql"),
                       "executed_at": evidence.get("executed_at"),
                       "coverage": evidence.get("coverage"),
                       "columns": evidence.get("columns"),
                       "preview_only": True}
            evidence_rows.extend(_evidence_chunks(m.id, "元数据", "", details))
            for index, data_row in enumerate(preview, 1):
                evidence_rows.extend(_evidence_chunks(m.id, "预览行", index, data_row))

    safe_name = session.name or "session"
    log_action(db, "export", "export", f"导出会话: {safe_name} ({body.format})", user_id=user.id)

    if body.format == "csv":
        content = _csv_bytes(rows)
        return _file_response(content, f"{safe_name}.csv", "text/csv; charset=utf-8")

    # Excel
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "会话记录"
    truncated = _write_excel_rows(ws, rows)
    if len(evidence_rows) > 1:
        detail = wb.create_sheet("查询证据")
        _write_excel_rows(detail, evidence_rows)
    note = wb.create_sheet("范围说明")
    _write_excel_rows(note, [
        ["说明", "查询证据仅包含回答时保存的结果预览，不代表数据库完整结果。"],
        ["JSON重组", "查询证据页中同一消息ID、记录类型和结果行号的JSON片段按片段序号拼接。"],
        ["生成输入", "会话记录中的生成输入快照属于回答时的模型、来源和问题上下文；超长快照以查询证据页片段为准。"],
        ["消息数", count], ["保存的预览行数", preview_count],
        ["主表超长单元格截断数", truncated],
        ["超长内容", "主表单元格超过Excel限制时有显式截断标记；CSV保留原文本。结果预览请以查询证据页为准。"],
    ])
    out = io.BytesIO()
    wb.save(out)
    return _file_response(
        out.getvalue(), f"{safe_name}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/export-result")
def export_result(body: schemas.ExportResultRequest, db: Session = Depends(get_db),
                  user=Depends(security.get_current_user)):
    message = (db.query(models.Message)
               .filter(models.Message.id == body.message_id,
                       models.Message.user_id == user.id,
                       models.Message.role == "assistant")
               .first())
    if message is None:
        raise api_error(404, "not_found", "回答不存在")
    try:
        evidence = json.loads(message.evidence_json or "")
        columns = evidence["columns"]
        data_rows = evidence["rows"]
        coverage = evidence["coverage"]
        if not isinstance(columns, list) or not isinstance(data_rows, list):
            raise ValueError("invalid evidence")
    except (ValueError, TypeError, KeyError):
        raise api_error(409, "no_result", "此回答没有可导出的查询结果")

    rows = [columns] + data_rows
    safe_name = f"查询结果预览-{message.id[:8]}"
    if body.format == "csv":
        # A typed metadata record keeps the CSV importable while making the
        # preview's scope visible even when the query returned zero rows.
        kind_name = "__qa_record_type"
        scope_name = "__qa_scope"
        while kind_name in columns:
            kind_name += "_"
        while scope_name in columns or scope_name == kind_name:
            scope_name += "_"
        scope = json.dumps({"source": _saved_source(message), "executed_at": evidence.get("executed_at"),
                            "sql": evidence.get("sql"),
                            "generated_sql": evidence.get("generated_sql"),
                            "coverage": coverage},
                           ensure_ascii=False)
        csv_rows = [columns + [kind_name, scope_name],
                    [""] * len(columns) + ["metadata", scope]]
        csv_rows.extend([list(row) + ["data", ""] for row in data_rows])
        return _file_response(_csv_bytes(csv_rows), f"{safe_name}.csv", "text/csv; charset=utf-8")

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "查询结果预览"
    truncated = _write_excel_rows(ws, rows)
    note = wb.create_sheet("范围说明")
    source = _saved_source(message)
    scope_rows = [
        ["说明", "此文件仅包含回答中保存的查询结果预览行。"],
        ["执行时间", evidence.get("executed_at") or ""],
        ["SQL", evidence.get("sql") or ""],
        ["模型原始 SQL", evidence.get("generated_sql") or ""],
        ["来源名称", source.get("name") or ""],
        ["来源类型", source.get("type") or ""],
        ["来源主机", source.get("host") or ""],
        ["来源端口", source.get("port") or ""],
        ["来源数据库", source.get("database") or ""],
        ["来源账号", source.get("username") or ""],
        ["来源文件", source.get("file_path") or ""],
        ["Excel 单元格截断数", truncated],
    ] + [[key, value] for key, value in coverage.items()]
    _write_excel_rows(note, scope_rows)
    out = io.BytesIO()
    wb.save(out)
    return _file_response(
        out.getvalue(), f"{safe_name}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _file_response(content: bytes, filename: str, media_type: str) -> Response:
    disposition = f"attachment; filename=\"export\"; filename*=UTF-8''{quote(filename)}"
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": disposition})
