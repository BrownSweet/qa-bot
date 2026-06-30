"""主数据库连接、会话与初始化。"""
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    # SQLite 在多线程（FastAPI）下需要关闭线程检查
    connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def gen_uuid() -> str:
    return str(uuid.uuid4())


def get_db():
    """FastAPI 依赖：提供数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """建表 + 写入默认系统配置。"""
    from . import models  # noqa: F401  确保模型已注册
    from .config import settings as _s

    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        if not db.query(models.SystemConfig).first():
            from .security import aes_encrypt
            cfg = models.SystemConfig(
                id=gen_uuid(),
                api_key=aes_encrypt(_s.DEEPSEEK_API_KEY) if _s.DEEPSEEK_API_KEY else "",
                api_url=_s.DEEPSEEK_API_URL,
                timeout=30,
            )
            db.add(cfg)
            db.commit()
    finally:
        db.close()
