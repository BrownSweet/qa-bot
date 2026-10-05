"""Local files are an explicit desktop capability, never an arbitrary server file browser."""
from pathlib import Path

from sqlalchemy.engine import make_url

from .config import settings
from .utils import api_error


def validate_data_file(file_path: str | None, kind: str) -> str:
    if not settings.ALLOW_LOCAL_FILES:
        raise api_error(403, "forbidden", "服务器模式未开启本地文件数据源")
    if not file_path or "\x00" in file_path:
        raise api_error(400, "bad_request", "请选择数据文件")
    path = Path(file_path).expanduser().resolve()
    database = make_url(settings.DATABASE_URL)
    internal_db = Path(database.database).resolve() if database.drivername.startswith("sqlite") and database.database not in (None, ":memory:") else None
    if (settings.DATA_DIR and path.is_relative_to(settings.DATA_DIR)) or path == internal_db:
        raise api_error(403, "forbidden", "不能把应用内部数据文件作为查询数据源")
    if not path.is_file():
        raise api_error(404, "not_found", "数据文件不存在或不是普通文件")
    if kind == "excel" and path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise api_error(400, "bad_request", "请选择 .xlsx 或 .xlsm 工作簿")
    if kind == "sqlite":
        with path.open("rb") as stream:
            if stream.read(16) != b"SQLite format 3\x00":
                raise api_error(400, "bad_request", "请选择有效的 SQLite 数据库")
    return str(path)
