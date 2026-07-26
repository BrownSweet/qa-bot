"""全局配置：从 .env 读取，提供默认值（最少配置即可启动）。"""
import os
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()


def _database_url() -> str:
    """生成系统主数据库连接；显式 DATABASE_URL 的优先级最高。"""
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
    DATABASE_URL: str = _database_url()
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
    AES_KEY: str = os.getenv("AES_KEY", "dev-aes-key-change-me")
    TOKEN_EXPIRE_HOURS: int = int(os.getenv("TOKEN_EXPIRE_HOURS", "24"))
    TOKEN_REMEMBER_HOURS: int = int(os.getenv("TOKEN_REMEMBER_HOURS", "720"))

    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_API_URL: str = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


settings = Settings()
