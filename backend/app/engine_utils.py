"""Read-only data sources, typed Excel imports, and bounded SQL execution."""
import json
import math
import re
import time
from datetime import date, datetime, time as datetime_time
from pathlib import Path
from typing import List, Tuple

import sqlglot
from sqlglot import exp
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, Integer, MetaData, Table, Text, Time,
    create_engine, event, inspect, text,
)
from sqlalchemy.engine import URL
from sqlalchemy.pool import StaticPool

from .security import aes_decrypt

DEFAULT_ROW_LIMIT = 200
MAX_ROW_LIMIT = 1000
QUERY_TIMEOUT_SECONDS = 15
MAX_EXCEL_ROWS = 200_000
MAX_SCHEMA_BYTES = 64 * 1024
MAX_CELL_BYTES = 8 * 1024
MAX_RESULT_BYTES = 256 * 1024


def _json_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def _fit_json_text(value: str, marker: str = "…（单元格已截断）") -> tuple[str, bool]:
    """Respect the cell budget after JSON quoting and escaping."""
    if _json_size(value) <= MAX_CELL_BYTES:
        return value, False
    low, high = 0, len(value)
    while low < high:
        mid = (low + high + 1) // 2
        if _json_size(value[:mid] + marker) <= MAX_CELL_BYTES:
            low = mid
        else:
            high = mid - 1
    return value[:low] + marker, True


def _bounded_text(value: str) -> tuple[str, bool]:
    # Encoding in chunks avoids a second huge allocation for one large TEXT cell.
    prefix = bytearray()
    total = 0
    for offset in range(0, len(value), 4096):
        chunk = value[offset:offset + 4096].encode("utf-8", "replace")
        total += len(chunk)
        if len(prefix) < MAX_CELL_BYTES:
            prefix.extend(chunk[:MAX_CELL_BYTES - len(prefix)])
    if total <= MAX_CELL_BYTES:
        return _fit_json_text(prefix.decode("utf-8", "replace"))
    marker = f"…（单元格已截断，原始 {total} 字节）"
    allowed = MAX_CELL_BYTES - len(marker.encode("utf-8")) - 2
    preview = prefix[:max(0, allowed)].decode("utf-8", "ignore") + marker
    fitted, _ = _fit_json_text(preview, marker)
    return fitted, True


def _bounded_cell(value):
    """Return JSON-safe values without retaining huge string or binary copies."""
    if value is None or isinstance(value, bool):
        return value, False
    if isinstance(value, int):
        if _json_size(value) <= MAX_CELL_BYTES:
            return value, False
        text_value, _ = _bounded_text(str(value))
        return text_value, True
    if isinstance(value, float):
        return (value, False) if math.isfinite(value) else (str(value), False)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = value if not isinstance(value, memoryview) else value.cast("B")
        if len(raw) * 2 + 4 <= MAX_CELL_BYTES:
            return "0x" + bytes(raw).hex(), False
        preview_bytes = max(0, (MAX_CELL_BYTES - 128) // 2)
        text_value = "0x" + bytes(raw[:preview_bytes]).hex()
        fitted, _ = _fit_json_text(text_value + f"…（二进制已截断，原始 {len(raw)} 字节）")
        return fitted, True
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, (dict, list, tuple)):
        chunks = []
        size = 0
        encoder = json.JSONEncoder(ensure_ascii=True, default=str)
        for chunk in encoder.iterencode(value):
            encoded = chunk.encode("utf-8", "replace")
            if size + len(encoded) > MAX_CELL_BYTES:
                prefix = "".join(chunks) + encoded[:MAX_CELL_BYTES - size].decode("utf-8", "ignore")
                fitted, _ = _fit_json_text(prefix, "…（JSON 单元格已截断）")
                return fitted, True
            chunks.append(chunk)
            size += len(encoded)
        return json.loads("".join(chunks)), False
    return _bounded_text(str(value))

# Unknown functions are rejected as well: a user-defined function may write or
# access files even when its enclosing statement is a SELECT.
SAFE_ANONYMOUS_FUNCTIONS = {
    "strftime", "julianday", "unixepoch", "timediff", "typeof", "ifnull",
    "json", "json_valid", "json_type", "json_array_length", "json_extract",
    "json_quote", "json_each", "json_tree", "group_concat", "printf", "format",
    "date_format", "str_to_date", "timestampdiff", "timestampadd", "week",
    "yearweek", "dayname", "monthname", "instr", "substr", "substring",
    "length", "char_length", "replace", "round", "abs", "floor", "ceil",
    "ceiling", "lower", "upper", "trim", "ltrim", "rtrim", "concat",
}
UNSAFE_FUNCTIONS = {
    "load_extension", "readfile", "writefile", "load_file", "sleep", "benchmark",
    "get_lock", "release_lock", "release_all_locks", "nextval", "setval",
    "set_config", "pg_notify", "lo_import", "lo_export", "lo_create",
    "lo_unlink", "dblink", "dblink_exec", "query_to_xml", "database_to_xml",
}


def _build_url(cfg: dict):
    """Use structured URLs so special characters in credentials stay literal."""
    kind = cfg["type"]
    if kind in {"mysql", "postgresql"}:
        return URL.create(
            "mysql+pymysql" if kind == "mysql" else "postgresql+psycopg2",
            username=cfg.get("username"), password=cfg.get("password") or "",
            host=cfg.get("host"), port=cfg.get("port") or (3306 if kind == "mysql" else 5432),
            database=cfg.get("database"),
        )
    if kind == "sqlite":
        selected = cfg.get("file_path") or cfg.get("database")
        if not selected:
            raise ValueError("请选择 SQLite 文件")
        path = Path(selected).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError("SQLite 数据源必须是文件")
        # URI quoting also handles spaces, #, ? and Windows drive letters.
        return f"sqlite:///{path.as_uri()}?mode=ro&uri=true"
    raise ValueError(f"不支持的数据库类型: {kind}")


def _safe_ident(name: str) -> str:
    return re.sub(r"[^\w一-鿿]", "_", str(name)).strip("_") or "col"


def _unique_ident(name, used: set) -> str:
    base = _safe_ident(name)
    candidate, suffix = base, 2
    while candidate.casefold() in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def _excel_type(values):
    present = [value for value in values if value is not None]
    if not present:
        return Text()
    if all(isinstance(value, bool) for value in present):
        return Boolean()
    if all(isinstance(value, int) for value in present):
        return Integer()
    if all(isinstance(value, (int, float)) for value in present):
        return Float()
    if all(isinstance(value, datetime) for value in present):
        return DateTime()
    if all(isinstance(value, date) and not isinstance(value, datetime) for value in present):
        return Date()
    if all(isinstance(value, datetime_time) for value in present):
        return Time()
    if all(isinstance(value, date) for value in present):
        return DateTime()
    return Text()


def _excel_value(value, column_type):
    if value is None:
        return None
    if isinstance(column_type, Text):
        return value.isoformat() if isinstance(value, (date, datetime_time)) else str(value)
    if isinstance(column_type, DateTime) and not isinstance(value, datetime):
        return datetime.combine(value, datetime_time())
    return value


def _excel_to_sqlite(file_path: str):
    """Preserve numeric/date affinity, then seal the in-memory database read-only."""
    from openpyxl import load_workbook

    if not file_path or not Path(file_path).is_file():
        raise FileNotFoundError("文件不存在")
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    workbook = None
    try:
        workbook = load_workbook(file_path, read_only=True, data_only=True)
        table_names = set()
        imported_rows = 0
        with engine.begin() as conn:
            for sheet in workbook.worksheets:
                iterator = sheet.iter_rows(values_only=True)
                raw_headers = next(iterator, None)
                if raw_headers is None:
                    continue
                rows = []
                for row in iterator:
                    imported_rows += 1
                    if imported_rows > MAX_EXCEL_ROWS:
                        raise ValueError(f"Excel 超过 {MAX_EXCEL_ROWS} 行，请缩小文件后重试")
                    rows.append(row)
                column_names = set()
                headers = [
                    _unique_ident(header if header is not None else f"col_{i + 1}", column_names)
                    for i, header in enumerate(raw_headers)
                ]
                types = [_excel_type(row[i] if i < len(row) else None for row in rows)
                         for i in range(len(headers))]
                table = Table(
                    _unique_ident(sheet.title, table_names), MetaData(),
                    *(Column(name, kind) for name, kind in zip(headers, types)),
                )
                table.create(conn)
                for start in range(0, len(rows), 500):
                    batch = [{name: _excel_value(row[i] if i < len(row) else None, types[i])
                              for i, name in enumerate(headers)} for row in rows[start:start + 500]]
                    conn.execute(table.insert(), batch)
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA query_only = ON")
        return engine
    except BaseException:
        engine.dispose()
        raise
    finally:
        if workbook is not None:
            workbook.close()


def build_engine(cfg: dict):
    """Build a source engine; never create or open a writable external SQLite DB."""
    if cfg["type"] == "excel":
        return _excel_to_sqlite(cfg.get("file_path"))
    decrypted = dict(cfg)
    decrypted["password"] = aes_decrypt(cfg.get("password") or "")
    kwargs = {"pool_pre_ping": True}
    if cfg["type"] == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": QUERY_TIMEOUT_SECONDS}
    elif cfg["type"] == "mysql":
        kwargs["connect_args"] = {
            "connect_timeout": QUERY_TIMEOUT_SECONDS,
            "read_timeout": QUERY_TIMEOUT_SECONDS + 1,
            "write_timeout": QUERY_TIMEOUT_SECONDS + 1,
        }
    elif cfg["type"] == "postgresql":
        kwargs["connect_args"] = {"connect_timeout": QUERY_TIMEOUT_SECONDS}
    engine = create_engine(_build_url(decrypted), **kwargs)
    if cfg["type"] == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_read_only(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA query_only = ON")
    return engine


def get_schema(engine) -> str:
    inspector = inspect(engine)
    lines: List[str] = []
    size = 0
    for table in inspector.get_table_names():
        columns = inspector.get_columns(table)
        description = ", ".join(f"{column['name']} {column['type']}" for column in columns)
        line = f"表 {table}({description})"
        size += len(line.encode("utf-8")) + 1
        if size > MAX_SCHEMA_BYTES:
            raise ValueError("数据表结构超过 64 KiB，请缩小数据源或只保留相关表后重试")
        lines.append(line)
    return "\n".join(lines) if lines else "（数据库中没有任何数据表）"


def validate_query(sql: str, dialect: str = "sqlite"):
    """Parse one query, recursively reject side effects, and return its AST."""
    dialect = "postgres" if dialect == "postgresql" else dialect
    if dialect not in {"sqlite", "mysql", "postgres"}:
        raise ValueError("不支持此数据库的只读查询")
    try:
        statements = sqlglot.parse(sql, read=dialect, error_level=sqlglot.ErrorLevel.RAISE)
    except sqlglot.errors.SqlglotError as error:
        raise ValueError("SQL 解析失败，请重新提问") from error
    if len(statements) != 1 or statements[0] is None:
        raise ValueError("仅允许一条只读查询语句")
    query = statements[0]
    while isinstance(query, exp.Subquery):
        query = query.this
    if not isinstance(query, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise ValueError("仅允许只读 SELECT 查询")
    forbidden_names = {
        "DML", "DDL", "Command", "Into", "Lock", "Transaction", "Commit", "Rollback",
        "Set", "Use", "Pragma", "Attach", "Detach", "Copy", "Execute", "Grant", "Revoke",
        "Hint", "PropertyEQ", "SessionParameter", "Parameter",
    }
    forbidden_types = tuple(getattr(exp, name) for name in forbidden_names if hasattr(exp, name))
    for node in query.walk():
        if isinstance(node, forbidden_types) or node.args.get("into") or node.args.get("locks"):
            raise ValueError("查询包含写入、锁定或其他不允许的操作")
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            raise ValueError("不允许调用指定命名空间的函数")
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in UNSAFE_FUNCTIONS or name.startswith(("pg_", "dblink", "lo_")):
                raise ValueError("查询包含不允许的函数")
            if isinstance(node, exp.Anonymous) and name not in SAFE_ANONYMOUS_FUNCTIONS:
                raise ValueError(f"暂不支持函数 {name}，请使用标准 SQL 聚合或日期函数")
    return query


def run_query_with_metadata(engine, sql: str, limit: int = DEFAULT_ROW_LIMIT,
                            timeout_seconds: float = QUERY_TIMEOUT_SECONDS):
    """Execute in a read-only transaction with a deadline and a server-side row cap."""
    row_limit = max(1, min(int(limit), MAX_ROW_LIMIT))
    timeout_seconds = max(0.001, min(float(timeout_seconds), 60))
    dialect = engine.dialect.name
    query = validate_query(sql, dialect)
    original_limit = query.args.get("limit")
    capped_limit = row_limit + 1
    if original_limit is not None:
        expression = original_limit.expression
        if isinstance(expression, exp.Literal) and expression.is_int:
            value = int(expression.this)
            if value >= 0:
                capped_limit = min(value, capped_limit)
    query = query.limit(capped_limit, copy=True)
    # Execute the validated tree, not the original string (including comments or hints).
    rendered = query.sql(dialect="postgres" if dialect == "postgresql" else dialect, comments=False)
    with engine.connect() as conn:
        sqlite_connection = None
        result = None
        try:
            if dialect == "sqlite":
                conn.exec_driver_sql("PRAGMA query_only = ON")
                sqlite_connection = conn.connection.driver_connection
                deadline = time.monotonic() + timeout_seconds
                sqlite_connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            elif dialect == "postgresql":
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_seconds * 1000)}")
            elif dialect == "mysql":
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_seconds * 1000)}")
            result = conn.execution_options(stream_results=True).execute(text(rendered))
            columns = list(result.keys())
            rows = []
            result_bytes = 2  # JSON array brackets; per-cell bytes include quoting.
            truncated_cell_count = 0
            result_truncated = False
            has_more = False
            while True:
                fetched = result.fetchone()
                if fetched is None:
                    break
                if len(rows) >= row_limit:
                    has_more = True
                    break
                values = []
                row_truncated_cells = 0
                for value in fetched:
                    bounded, truncated = _bounded_cell(value)
                    values.append(bounded)
                    row_truncated_cells += int(truncated)
                row_bytes = _json_size(values)
                next_size = result_bytes + row_bytes + (2 if rows else 0)
                if next_size > MAX_RESULT_BYTES:
                    result_truncated = True
                    has_more = True
                    break
                rows.append(values)
                result_bytes = next_size
                truncated_cell_count += row_truncated_cells
            metadata = {
                "row_limit": row_limit, "returned_rows": len(rows),
                "has_more": has_more, "executed_sql": rendered,
                "cell_byte_limit": MAX_CELL_BYTES,
                "result_byte_limit": MAX_RESULT_BYTES,
                "result_bytes": result_bytes,
                "cells_truncated": truncated_cell_count > 0,
                "truncated_cell_count": truncated_cell_count,
                "result_truncated": result_truncated,
            }
            return columns, rows, metadata
        finally:
            if result is not None:
                result.close()
            if sqlite_connection is not None:
                sqlite_connection.set_progress_handler(None, 0)
            conn.rollback()


def run_query(engine, sql: str, limit: int = DEFAULT_ROW_LIMIT) -> Tuple[List[str], List[list]]:
    """Compatibility API: return column names and at most limit rows."""
    columns, rows, _metadata = run_query_with_metadata(engine, sql, limit)
    return columns, rows
