"""数据导入：解析 Excel / 预览工作表数据。"""
import os

from fastapi import APIRouter, Depends

from .. import engine_utils, schemas, security
from ..utils import api_error
from ..file_policy import validate_data_file

router = APIRouter(prefix="/api/excel", tags=["excel"])


def _detect_type(value_types) -> str:
    if not value_types:
        return "string"
    if all(issubclass(kind, (int, float)) for kind in value_types):
        return "number"
    from datetime import date, datetime
    if all(issubclass(kind, (date, datetime)) for kind in value_types):
        return "date"
    return "string"


@router.post("/parse")
def parse_excel(body: schemas.ParseExcelRequest, user=Depends(security.get_current_user)):
    body.file_path = validate_data_file(body.file_path, "excel")
    if not os.path.exists(body.file_path):
        raise api_error(404, "not_found", "文件不存在")
    from openpyxl import load_workbook
    try:
        wb = load_workbook(body.file_path, read_only=True, data_only=True)
    except Exception as e:
        raise api_error(400, "bad_request", f"无法解析文件：{e}")
    sheets = []
    for ws in wb.worksheets:
        sheets.append({"name": ws.title, "row_count": ws.max_row or 0, "column_count": ws.max_column or 0})
    wb.close()
    return {"sheets": sheets}


@router.post("/sheet-data")
def sheet_data(body: schemas.GetSheetDataRequest, user=Depends(security.get_current_user)):
    body.file_path = validate_data_file(body.file_path, "excel")
    if not os.path.exists(body.file_path):
        raise api_error(404, "not_found", "文件不存在")
    from openpyxl import load_workbook
    wb = load_workbook(body.file_path, read_only=True, data_only=True)
    try:
        if body.sheet_name not in wb.sheetnames:
            raise api_error(404, "not_found", "工作表不存在")
        iterator = wb[body.sheet_name].iter_rows(values_only=True)
        header_row = next(iterator, None)
        if header_row is None:
            return {"columns": [], "rows": [], "total_rows": 0,
                    "coverage": {"returned_rows": 0, "has_more": False}}

        headers = [str(value) if value is not None else f"col_{i}"
                   for i, value in enumerate(header_row)]
        if sum(len(name.encode("utf-8")) + 1 for name in headers) > engine_utils.MAX_SCHEMA_BYTES:
            raise api_error(400, "bad_request", "工作表列信息超过64 KiB，请只保留必要列")
        samples = [[] for _ in headers]
        preview = []
        total_rows = 0
        result_bytes = 2
        truncated_cells = 0
        result_truncated = False
        for row in iterator:
            total_rows += 1
            if total_rows > engine_utils.MAX_EXCEL_ROWS:
                raise api_error(400, "bad_request", "工作表超过200000行，请缩小文件后重试")
            for index, sample in enumerate(samples):
                if len(sample) < 20 and index < len(row) and row[index] is not None:
                    sample.append(type(row[index]))
            if len(preview) >= body.limit or result_truncated:
                continue
            bounded = []
            cell_count = 0
            for value in row:
                cell, clipped = engine_utils._bounded_cell(value)
                bounded.append(cell)
                cell_count += int(clipped)
            next_size = result_bytes + engine_utils._json_size(bounded) + (2 if preview else 0)
            if next_size > engine_utils.MAX_RESULT_BYTES:
                result_truncated = True
                continue
            preview.append(bounded)
            result_bytes = next_size
            truncated_cells += cell_count
        columns = [{"name": name, "index": index, "type": _detect_type(samples[index])}
                   for index, name in enumerate(headers)]
        return {"columns": columns, "rows": preview, "total_rows": total_rows,
                "coverage": {"row_limit": body.limit, "returned_rows": len(preview),
                             "has_more": total_rows > len(preview),
                             "cells_truncated": truncated_cells > 0,
                             "result_truncated": result_truncated,
                             "result_byte_limit": engine_utils.MAX_RESULT_BYTES}}
    finally:
        wb.close()
