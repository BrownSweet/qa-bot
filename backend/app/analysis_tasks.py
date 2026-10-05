"""Reusable parameterized questions linked to real chat runs and evidence."""
import json
import re
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Session

from . import ai, models, security
from .database import Base, gen_uuid, get_db
from .source_identity import source_identity_json, source_identity_matches
from .utils import api_error, log_action

router = APIRouter(prefix="/api/analysis-tasks", tags=["analysis-tasks"])
PLACEHOLDER = re.compile(r"\{\{([A-Za-z][A-Za-z0-9_]{0,31})\}\}")


def _parameter_names(template: str) -> set[str]:
    names = set(PLACEHOLDER.findall(template))
    remaining = PLACEHOLDER.sub("", template)
    if "{{" in remaining or "}}" in remaining:
        raise api_error(400, "bad_request", "模板变量须写成 {{name}}，名称以英文字母开头且不超过32字符")
    return names


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    db_config_id = Column(String(36), nullable=False)
    name = Column(String(100), nullable=False)
    question_template = Column(Text, nullable=False)
    source_identity_json = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime)


class TaskWrite(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    question_template: str = Field(min_length=1, max_length=1000)
    db_config_id: str = Field(min_length=1, max_length=36)


class PrepareRun(BaseModel):
    parameters: dict[str, str] = Field(default_factory=dict)


def _task(db: Session, task_id: str, user_id: str) -> AnalysisTask:
    task = db.query(AnalysisTask).filter(
        AnalysisTask.id == task_id,
        AnalysisTask.user_id == user_id,
        AnalysisTask.deleted_at.is_(None),
    ).first()
    if task is None:
        raise api_error(404, "not_found", "分析任务不存在")
    return task


def _source(db: Session, source_id: str, user_id: str):
    source = db.query(models.DBConfig).filter(
        models.DBConfig.id == source_id, models.DBConfig.user_id == user_id,
    ).first()
    if source is None:
        raise api_error(404, "not_found", "数据源不存在或已删除")
    return source


def _serialize(task: AnalysisTask, source=None) -> dict:
    return {
        "id": task.id, "name": task.name,
        "question_template": task.question_template,
        "db_config_id": task.db_config_id,
        "parameters": sorted(set(PLACEHOLDER.findall(task.question_template))),
        "source_changed": source is None or not source_identity_matches(task.source_identity_json, source),
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def verify_task_source(task: AnalysisTask, source: models.DBConfig) -> None:
    if not source_identity_matches(task.source_identity_json, source):
        raise api_error(409, "source_changed", "分析任务绑定的数据源已变化，请核对模板并重新保存任务")


def render_question(task: AnalysisTask, parameters: dict[str, str]) -> str:
    """Use one strict expansion rule in both preview and actual chat submission."""
    names = _parameter_names(task.question_template)
    if set(parameters) != names:
        raise api_error(400, "bad_request", "参数必须与模板变量完全一致")
    if any(len(value) > 200 or not value.strip() for value in parameters.values()):
        raise api_error(400, "bad_request", "参数值不能为空且不能超过200字")
    question = PLACEHOLDER.sub(lambda match: parameters[match.group(1)], task.question_template)
    if len(question) > 1000:
        raise api_error(400, "bad_request", "展开后的问题不能超过1000字")
    return question


@router.get("")
def list_tasks(db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    tasks = db.query(AnalysisTask).filter(
        AnalysisTask.user_id == user.id, AnalysisTask.deleted_at.is_(None),
    ).order_by(AnalysisTask.updated_at.desc()).all()
    source_ids = {task.db_config_id for task in tasks}
    sources = {source.id: source for source in db.query(models.DBConfig).filter(
        models.DBConfig.user_id == user.id, models.DBConfig.id.in_(source_ids),
    ).all()} if source_ids else {}
    return {"tasks": [_serialize(task, sources.get(task.db_config_id)) for task in tasks]}


@router.post("", status_code=201)
def create_task(body: TaskWrite, db: Session = Depends(get_db),
                user=Depends(security.get_current_user)):
    source = _source(db, body.db_config_id, user.id)
    if not body.name.strip() or not body.question_template.strip():
        raise api_error(400, "bad_request", "任务名称和问题模板不能为空")
    _parameter_names(body.question_template)
    task = AnalysisTask(
        id=gen_uuid(), user_id=user.id, db_config_id=body.db_config_id,
        name=body.name.strip(), question_template=body.question_template.strip(),
        source_identity_json=source_identity_json(source),
    )
    db.add(task)
    db.commit()
    log_action(db, "create_analysis_task", "analysis", f"收藏分析任务: {task.name}", user_id=user.id)
    return {"task": _serialize(task, source)}


@router.get("/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db),
             user=Depends(security.get_current_user)):
    task = _task(db, task_id, user.id)
    source = db.query(models.DBConfig).filter(
        models.DBConfig.id == task.db_config_id, models.DBConfig.user_id == user.id,
    ).first()
    return {"task": _serialize(task, source)}


@router.put("/{task_id}")
def update_task(task_id: str, body: TaskWrite, db: Session = Depends(get_db),
                user=Depends(security.get_current_user)):
    task = _task(db, task_id, user.id)
    source = _source(db, body.db_config_id, user.id)
    if not body.name.strip() or not body.question_template.strip():
        raise api_error(400, "bad_request", "任务名称和问题模板不能为空")
    _parameter_names(body.question_template)
    task.name = body.name.strip()
    task.question_template = body.question_template.strip()
    task.db_config_id = body.db_config_id
    task.source_identity_json = source_identity_json(source)
    task.updated_at = datetime.utcnow()
    db.commit()
    return {"task": _serialize(task, source)}


@router.delete("/{task_id}")
def delete_task(task_id: str, db: Session = Depends(get_db),
                user=Depends(security.get_current_user)):
    task = _task(db, task_id, user.id)
    task.deleted_at = datetime.utcnow()
    db.commit()
    return {"message": "分析任务已删除；原会话记录仍保留"}


@router.post("/{task_id}/prepare")
def prepare_run(task_id: str, body: PrepareRun, db: Session = Depends(get_db),
                user=Depends(security.get_current_user)):
    task = _task(db, task_id, user.id)
    source = _source(db, task.db_config_id, user.id)
    verify_task_source(task, source)
    question = render_question(task, body.parameters)
    return {"question": question, "db_config_id": task.db_config_id,
            "analysis_task_id": task.id}


@router.get("/{task_id}/runs")
def list_runs(task_id: str, page: int = 1, limit: int = 30,
              db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    _task(db, task_id, user.id)
    page = max(1, page)
    limit = max(1, min(100, limit))
    query = db.query(models.Message).filter(
        models.Message.user_id == user.id,
        models.Message.analysis_task_id == task_id,
        models.Message.role == "assistant",
    )
    total = query.count()
    messages = query.order_by(models.Message.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    question_ids = [message.question_message_id for message in messages if message.question_message_id]
    questions = {message.id: message.content for message in db.query(models.Message).filter(
        models.Message.id.in_(question_ids), models.Message.user_id == user.id,
    ).all()} if question_ids else {}
    runs = []
    for message in messages:
        runs.append({
            "message_id": message.id,
            "session_id": message.session_id,
            "db_config_id": message.db_config_id,
            "source": json.loads(message.source_snapshot_json) if message.source_snapshot_json else None,
            "question": questions.get(message.question_message_id, ""),
            "content": message.content,
            "status": message.status,
            "evidence": json.loads(message.evidence_json) if message.evidence_json else None,
            "generation_snapshot": ai.visible_generation_snapshot(
                json.loads(message.generation_snapshot_json) if message.generation_snapshot_json else None
            ),
            "created_at": message.created_at.isoformat(),
        })
    return {"runs": runs, "total": total, "page": page, "limit": limit}
