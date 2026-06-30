"""全局配置：从 .env 读取，提供默认值（最少配置即可启动）。"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./qabot.db")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
    AES_KEY: str = os.getenv("AES_KEY", "dev-aes-key-change-me")
    TOKEN_EXPIRE_HOURS: int = int(os.getenv("TOKEN_EXPIRE_HOURS", "24"))
    TOKEN_REMEMBER_HOURS: int = int(os.getenv("TOKEN_REMEMBER_HOURS", "720"))

    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_API_URL: str = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


settings = Settings()
