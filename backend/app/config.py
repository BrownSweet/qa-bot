"""全局配置：从 .env 读取，提供默认值（最少配置即可启动）。"""
import os
import json
import secrets
import tempfile
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

DESKTOP_MODE = os.getenv("QA_DESKTOP_MODE") == "1"
DATA_DIR = Path(os.environ["QA_DATA_DIR"]).resolve() if DESKTOP_MODE else None


def _desktop_secrets() -> dict:
    """Generate installation-specific keys; never package a shared secret or read a developer .env."""
    if not DESKTOP_MODE:
        return {}
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    from .workspace import recover_interrupted_restore
    recover_interrupted_restore(DATA_DIR)
    path = DATA_DIR / "secrets.json"
    if not path.exists():
        if (DATA_DIR / "qabot.db").exists() and (DATA_DIR / "qabot.db").stat().st_size:
            raise RuntimeError("本地数据库已存在，但 secrets.json 密钥缺失。为避免损坏现有凭证，已停止启动；请使用完整备份恢复，不能重新生成密钥。")
        descriptor, temporary = tempfile.mkstemp(prefix=".secrets-", dir=DATA_DIR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"jwt": secrets.token_hex(32), "aes": secrets.token_hex(32)}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    try:
        with path.open(encoding="utf-8") as stream:
            keys = json.load(stream)
    except (OSError, ValueError) as error:
        raise RuntimeError("本地密钥文件无法读取，请通过完整备份恢复，勿删除现有数据库或密钥") from error
    if not isinstance(keys, dict) or any(not isinstance(keys.get(name), str) or len(keys[name]) < 64 for name in ("jwt", "aes")):
        raise RuntimeError("本地密钥文件无效，请从备份恢复，勿删除现有密钥")
    return keys


if not DESKTOP_MODE:
    load_dotenv()
_local_keys = _desktop_secrets()


def _database_url() -> str:
    """生成系统主数据库连接；显式 DATABASE_URL 的优先级最高。"""
    if DESKTOP_MODE:
        return f"sqlite:///{(DATA_DIR / 'qabot.db').as_posix()}"
    configured_url = os.getenv("DATABASE_URL", "").strip()
    if configured_url:
        return configured_url

    db_type = os.getenv("DB_TYPE", "sqlite").strip().lower()
    if db_type == "sqlite":
        sqlite_path = os.getenv("SQLITE_PATH", "./qabot.db").strip()
        if sqlite_path.startswith("/"):
            return f"sqlite:////{sqlite_path.lstrip('/')}"
        return f"sqlite:///{sqlite_path}"

    if db_type == "mysql":
        user = quote(os.getenv("MYSQL_USER", "qabot"), safe="")
        password = quote(os.getenv("MYSQL_PASSWORD", "change-me"), safe="")
        host = os.getenv("MYSQL_HOST", "127.0.0.1").strip()
        port = int(os.getenv("MYSQL_PORT", "3306"))
        database = quote(os.getenv("MYSQL_DATABASE", "qabot"), safe="")
        return (
            f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
            "?charset=utf8mb4"
        )

    raise ValueError("DB_TYPE 仅支持 sqlite 或 mysql")


class Settings:
    DESKTOP_MODE: bool = DESKTOP_MODE
    DATA_DIR = DATA_DIR
    DESKTOP_TOKEN: str = os.getenv("QA_DESKTOP_TOKEN", "")
    WEB_DIR: str = os.getenv("QA_WEB_DIR", "")
    VERSION: str = "1.2.1"
    DATABASE_URL: str = _database_url()
    SECRET_KEY: str = _local_keys.get("jwt") or os.getenv("SECRET_KEY", "dev-secret-key-change-me")
    AES_KEY: str = _local_keys.get("aes") or os.getenv("AES_KEY", "dev-aes-key-change-me")
    DEV_AUTH: bool = not DESKTOP_MODE and os.getenv("QA_DEV_AUTH") == "1"
    ADMIN_USER_IDS: tuple = tuple(filter(None, os.getenv("QA_ADMIN_USER_IDS", "").split(",")))
    ALLOW_LOCAL_FILES: bool = DESKTOP_MODE or os.getenv("QA_ALLOW_LOCAL_FILES") == "1"
    TOKEN_EXPIRE_HOURS: int = int(os.getenv("TOKEN_EXPIRE_HOURS", "24"))
    TOKEN_REMEMBER_HOURS: int = int(os.getenv("TOKEN_REMEMBER_HOURS", "720"))

    DEEPSEEK_API_KEY: str = "" if DESKTOP_MODE else os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_API_URL: str = "https://api.deepseek.com" if DESKTOP_MODE else os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL: str = "deepseek-chat" if DESKTOP_MODE else os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


settings = Settings()
if settings.DESKTOP_MODE and len(settings.DESKTOP_TOKEN) < 32:
    raise RuntimeError("桌面服务只能由应用启动：缺少本次启动的访问凭证")
