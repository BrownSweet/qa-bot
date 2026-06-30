"""数据导出：将会话消息导出为 Excel 或 CSV。"""
import csv
import io
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..database import get_db
from ..utils import api_error, log_action

router = APIRouter(prefix="/api", tags=["export"])


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

    messages = (
        db.query(models.Message)
        .filter(models.Message.session_id == session.id)
        .order_by(models.Message.created_at.asc())
        .all()
    )
    rows = [["角色", "内容", "状态", "时间"]]
    for m in messages:
        role = "用户" if m.role == "user" else "助手"
        rows.append([role, m.content, m.status, m.created_at.isoformat()])

    safe_name = session.name or "session"
    log_action(db, "export", "export", f"导出会话: {safe_name} ({body.format})", user_id=user.id)

    if body.format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerows(rows)
        content = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM 保证 Excel 正确识别中文
        return _file_response(content, f"{safe_name}.csv", "text/csv; charset=utf-8")

    # Excel
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "会话记录"
    for row in rows:
        ws.append(row)
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
