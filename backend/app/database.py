"""主数据库连接、会话与初始化。"""
import uuid

from sqlalchemy import create_engine, event, inspect, text
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

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

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

    prepare_schema(engine, _s)

    db = SessionLocal()
    try:
        if not db.query(models.SystemConfig).first():
            from .security import aes_encrypt
            cfg = models.SystemConfig(
                id=gen_uuid(),
                api_key=aes_encrypt(_s.DEEPSEEK_API_KEY) if _s.DEEPSEEK_API_KEY else "",
                api_url=_s.DEEPSEEK_API_URL,
                model=_s.DEEPSEEK_MODEL,
                timeout=30,
            )
            db.add(cfg)
            db.commit()
        if _s.DESKTOP_MODE and not db.get(models.User, "desktop-owner"):
            import secrets
            from .security import hash_password
            db.add(models.User(
                id="desktop-owner", username="本地用户", phone="00000000000",
                password_hash=hash_password(secrets.token_hex(24)),
            ))
            db.commit()
        if _s.DESKTOP_MODE:
            # Single desktop instance: a previous pending answer was interrupted
            # by a crash/forced quit and must not remain generating forever.
            db.query(models.Message).filter(models.Message.status == "pending").update({"status": "cancelled"})
            db.commit()
            from .quality_eval import recover_pending_runs
            recover_pending_runs(db, include_recent=True)
    finally:
        db.close()


def prepare_schema(database_engine, config):
    """Versioned additive migrations; refuse future schemas and back up desktop upgrades."""
    from .workspace import CURRENT_SCHEMA_VERSION, create_backup
    from datetime import datetime, timezone
    from pathlib import Path
    tables = set(inspect(database_engine).get_table_names())
    with database_engine.connect() as connection:
        current = connection.execute(text("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")).scalar() if "schema_migrations" in tables else (1 if tables else 0)
    if current > CURRENT_SCHEMA_VERSION:
        raise RuntimeError("数据库来自更新版本，请升级应用；已阻止降级启动")
    if config.DESKTOP_MODE and tables and current < CURRENT_SCHEMA_VERSION:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        create_backup(config.DATA_DIR, Path(config.DATA_DIR) / "backups" / f"pre-upgrade-v{current}-{stamp}.zip", config.VERSION)
    Base.metadata.create_all(bind=database_engine)
    additions = {
        "sessions": {"db_config_id": "VARCHAR(36)"},
        "messages": {"db_config_id": "VARCHAR(36)", "source_snapshot_json": "TEXT", "question_message_id": "VARCHAR(36)", "evidence_json": "TEXT", "analysis_task_id": "VARCHAR(36)"},
        "system_configs": {"model": "VARCHAR(100)"},
    }
    with database_engine.begin() as connection:
        connection.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at VARCHAR(40) NOT NULL)"))
        if current < 2:
            for table, columns in additions.items():
                existing = {column["name"] for column in inspect(connection).get_columns(table)}
                for name, sql_type in columns.items():
                    if name not in existing:
                        connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sql_type} NULL'))
            connection.execute(text("UPDATE system_configs SET model = :model WHERE model IS NULL OR model = ''"), {"model": getattr(config, "DEEPSEEK_MODEL", "deepseek-chat")})
        if current < 3:
            available = set(inspect(connection).get_table_names())
            for table in ("source_semantics", "analysis_tasks"):
                if table in available and "source_identity_json" not in {column["name"] for column in inspect(connection).get_columns(table)}:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN source_identity_json TEXT NULL"))
        if current < 4:
            available = set(inspect(connection).get_table_names())
            if "evaluation_runs" in available and "case_snapshot_json" not in {column["name"] for column in inspect(connection).get_columns("evaluation_runs")}:
                connection.execute(text("ALTER TABLE evaluation_runs ADD COLUMN case_snapshot_json TEXT NULL"))
        if current < 5:
            available = set(inspect(connection).get_table_names())
            for table in ("messages", "evaluation_runs"):
                if table in available and "generation_snapshot_json" not in {
                    column["name"] for column in inspect(connection).get_columns(table)
                }:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN generation_snapshot_json TEXT NULL"))
        for version in range(max(current, 0) + 1, CURRENT_SCHEMA_VERSION + 1):
            connection.execute(text("INSERT INTO schema_migrations (version, applied_at) VALUES (:version, :stamp)"), {"version": version, "stamp": datetime.now(timezone.utc).isoformat()})
