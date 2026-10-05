"""User-authored business meaning for a particular source's tables and metrics."""
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Session

from . import models, security
from .database import Base, gen_uuid, get_db
from .source_identity import source_identity_json, source_identity_matches
from .utils import api_error

router = APIRouter(prefix="/api/db/configs", tags=["db-semantics"])


class SourceSemantics(Base):
    __tablename__ = "source_semantics"
    __table_args__ = (UniqueConstraint("user_id", "db_config_id", name="uq_semantics_owner_source"),)
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    db_config_id = Column(String(36), ForeignKey("db_configs.id", ondelete="CASCADE"), nullable=False)
    context = Column(Text, nullable=False, default="")
    source_identity_json = Column(Text)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SemanticsWrite(BaseModel):
    context: str = Field(max_length=12000)


def _source(db: Session, config_id: str, user_id: str):
    source = db.query(models.DBConfig).filter(
        models.DBConfig.id == config_id, models.DBConfig.user_id == user_id,
    ).first()
    if source is None:
        raise api_error(404, "not_found", "数据源不存在")
    return source


def get_context(db: Session, config_id: str, user_id: str) -> str:
    source = db.query(models.DBConfig).filter(
        models.DBConfig.id == config_id, models.DBConfig.user_id == user_id,
    ).first()
    if source is None:
        return ""
    record = db.query(SourceSemantics).filter(
        SourceSemantics.user_id == user_id, SourceSemantics.db_config_id == config_id,
    ).first()
    return record.context if record and source_identity_matches(record.source_identity_json, source) else ""


@router.get("/{config_id}/semantics")
def read_semantics(config_id: str, db: Session = Depends(get_db),
                   user=Depends(security.get_current_user)):
    source = _source(db, config_id, user.id)
    record = db.query(SourceSemantics).filter(
        SourceSemantics.user_id == user.id, SourceSemantics.db_config_id == config_id,
    ).first()
    stale = bool(record and not source_identity_matches(record.source_identity_json, source))
    return {"context": record.context if record else "", "stale": stale,
            "updated_at": record.updated_at.isoformat() if record else None}


@router.put("/{config_id}/semantics")
def write_semantics(config_id: str, body: SemanticsWrite,
                    db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    source = _source(db, config_id, user.id)
    record = db.query(SourceSemantics).filter(
        SourceSemantics.user_id == user.id, SourceSemantics.db_config_id == config_id,
    ).first()
    if record is None:
        record = SourceSemantics(id=gen_uuid(), user_id=user.id, db_config_id=config_id,
                                 context=body.context, source_identity_json=source_identity_json(source))
        db.add(record)
    else:
        record.context = body.context
        record.source_identity_json = source_identity_json(source)
        record.updated_at = datetime.utcnow()
    db.commit()
    return {"context": record.context, "stale": False,
            "updated_at": record.updated_at.isoformat()}
