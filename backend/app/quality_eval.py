"""Golden questions compare generated SQL results with owner-approved SQL results."""
import asyncio
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Session

from . import ai, engine_utils, models, security
from .database import Base, SessionLocal, gen_uuid, get_db
from .routers.db_config import _engine_config
from .semantic_context import get_context
from .utils import api_error

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])


class EvaluationCase(Base):
    __tablename__ = "evaluation_cases"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    db_config_id = Column(String(36), nullable=False)
    question = Column(Text, nullable=False)
    expected_sql = Column(Text, nullable=False)
    baseline_json = Column(Text, nullable=False)
    source_identity_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    case_id = Column(String(36), ForeignKey("evaluation_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    generated_sql = Column(Text)
    case_snapshot_json = Column(Text)
    generation_snapshot_json = Column(Text)
    actual_json = Column(Text)
    status = Column(String(20), nullable=False)
    matched = Column(Boolean, nullable=False, default=False)
    baseline_changed = Column(Boolean, nullable=False, default=False)
    error = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class CaseWrite(BaseModel):
    db_config_id: str = Field(min_length=1, max_length=36)
    question: str = Field(min_length=1, max_length=1000)
    expected_sql: str = Field(min_length=1, max_length=5000)


class CaseImport(BaseModel):
    cases: list[CaseWrite] = Field(min_length=1, max_length=100)


def _source(db: Session, config_id: str, user_id: str) -> models.DBConfig:
    source = db.query(models.DBConfig).filter(
        models.DBConfig.id == config_id, models.DBConfig.user_id == user_id,
    ).first()
    if source is None:
        raise api_error(404, "not_found", "数据源不存在")
    return source


def _case(db: Session, case_id: str, user_id: str) -> EvaluationCase:
    case = db.query(EvaluationCase).filter(
        EvaluationCase.id == case_id, EvaluationCase.user_id == user_id,
    ).first()
    if case is None:
        raise api_error(404, "not_found", "评测样例不存在")
    return case


def _query(engine, sql: str) -> dict:
    columns, rows, coverage = engine_utils.run_query_with_metadata(engine, sql)
    if coverage["has_more"]:
        raise ValueError("评测结果超过行数或字节范围，请用可核对的聚合查询缩小范围")
    if coverage.get("cells_truncated") or coverage.get("result_truncated"):
        raise ValueError("评测结果包含被截断的单元格，不能作为标准答案，请缩小查询范围")
    return {"columns": columns, "rows": rows, "coverage": coverage}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _canonical_rows(result: dict, *, ordered: bool = False) -> list[str]:
    rows = [_json(row) for row in result["rows"]]
    return rows if ordered else sorted(rows)


def _same_columns(left: dict, right: dict) -> bool:
    """Column names and positions carry meaning even when cell values coincide."""
    return [str(name).casefold() for name in left["columns"]] == [
        str(name).casefold() for name in right["columns"]
    ]


def _same_result(left: dict, right: dict, *, ordered: bool) -> bool:
    return _same_columns(left, right) and (
        _canonical_rows(left, ordered=ordered) == _canonical_rows(right, ordered=ordered)
    )


def _source_identity(source: models.DBConfig) -> dict:
    return {"type": source.type, "host": source.host, "port": source.port,
            "database": source.database_name, "username": source.username,
            "file_path": source.file_path}


def _serialize_case(case: EvaluationCase) -> dict:
    return {
        "id": case.id, "db_config_id": case.db_config_id,
        "question": case.question, "expected_sql": case.expected_sql,
        "baseline": json.loads(case.baseline_json),
        "source_identity": json.loads(case.source_identity_json),
        "created_at": case.created_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
    }


def _serialize_run(run: EvaluationRun) -> dict:
    return {
        "id": run.id, "case_id": run.case_id, "generated_sql": run.generated_sql,
        "case_snapshot": json.loads(run.case_snapshot_json) if run.case_snapshot_json else None,
        "generation_snapshot": ai.visible_generation_snapshot(
            json.loads(run.generation_snapshot_json) if run.generation_snapshot_json else None
        ),
        "actual": json.loads(run.actual_json) if run.actual_json else None,
        "status": run.status, "matched": run.matched,
        "baseline_changed": run.baseline_changed, "error": run.error,
        "created_at": run.created_at.isoformat(),
    }


def recover_pending_runs(db: Session, *, case_id: str | None = None,
                         include_recent: bool = False) -> int:
    """Resolve abandoned evaluations without touching active Web workers' runs."""
    query = db.query(EvaluationRun).filter(EvaluationRun.status == "pending")
    if case_id is not None:
        query = query.filter(EvaluationRun.case_id == case_id)
    if not include_recent:
        query = query.filter(EvaluationRun.created_at < datetime.utcnow() - timedelta(minutes=30))
    count = query.update({
        "status": "error", "matched": False,
        "error": "评测运行中断，无法确认结果；生成输入已保留，请重新运行",
    }, synchronize_session="fetch")
    if count:
        db.commit()
    return count


@router.get("/cases")
def list_cases(db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    cases = db.query(EvaluationCase).filter(EvaluationCase.user_id == user.id).order_by(
        EvaluationCase.created_at.desc(),
    ).all()
    return {"cases": [_serialize_case(case) for case in cases]}


@router.post("/cases", status_code=201)
def create_case(body: CaseWrite, db: Session = Depends(get_db),
                user=Depends(security.get_current_user)):
    source = _source(db, body.db_config_id, user.id)
    engine = engine_utils.build_engine(_engine_config(source))
    try:
        baseline = _query(engine, body.expected_sql)
    except (ValueError, RuntimeError) as error:
        raise api_error(400, "bad_request", f"标准查询无效：{error}") from error
    finally:
        engine.dispose()
    baseline_json = _json(baseline)
    if len(baseline_json.encode("utf-8")) > 1_000_000:
        raise api_error(400, "bad_request", "标准结果过大，请缩小评测查询")
    case = EvaluationCase(
        id=gen_uuid(), user_id=user.id, db_config_id=source.id,
        question=body.question.strip(), expected_sql=body.expected_sql.strip(),
        baseline_json=baseline_json,
        source_identity_json=_json(_source_identity(source)),
    )
    if not case.question:
        raise api_error(400, "bad_request", "问题不能为空")
    db.add(case)
    db.commit()
    return {"case": _serialize_case(case)}


@router.post("/cases/import", status_code=201)
def import_cases(body: CaseImport, db: Session = Depends(get_db),
                 user=Depends(security.get_current_user)):
    """Validate every golden query before committing any case in the batch."""
    sources = {}
    engines = {}
    cases = []
    try:
        for item in body.cases:
            if not item.question.strip():
                raise api_error(400, "bad_request", "标准问题不能为空")
            if item.db_config_id not in sources:
                source = _source(db, item.db_config_id, user.id)
                sources[source.id] = source
                engines[source.id] = engine_utils.build_engine(_engine_config(source))
            source = sources[item.db_config_id]
            try:
                baseline = _query(engines[source.id], item.expected_sql)
            except (ValueError, RuntimeError) as error:
                raise api_error(400, "bad_request", f"标准查询无效：{error}") from error
            baseline_json = _json(baseline)
            if len(baseline_json.encode("utf-8")) > 1_000_000:
                raise api_error(400, "bad_request", "标准结果过大，请缩小评测查询")
            cases.append(EvaluationCase(
                id=gen_uuid(), user_id=user.id, db_config_id=source.id,
                question=item.question.strip(), expected_sql=item.expected_sql.strip(),
                baseline_json=baseline_json,
                source_identity_json=_json(_source_identity(source)),
            ))
        db.add_all(cases)
        db.commit()
        return {"cases": [_serialize_case(case) for case in cases]}
    finally:
        for engine in engines.values():
            engine.dispose()


@router.put("/cases/{case_id}")
def update_case(case_id: str, body: CaseWrite, db: Session = Depends(get_db),
                user=Depends(security.get_current_user)):
    case = _case(db, case_id, user.id)
    source = _source(db, body.db_config_id, user.id)
    engine = engine_utils.build_engine(_engine_config(source))
    try:
        baseline = _query(engine, body.expected_sql)
    except (ValueError, RuntimeError) as error:
        raise api_error(400, "bad_request", f"标准查询无效：{error}") from error
    finally:
        engine.dispose()
    baseline_json = _json(baseline)
    if len(baseline_json.encode("utf-8")) > 1_000_000:
        raise api_error(400, "bad_request", "标准结果过大，请缩小评测查询")
    if not body.question.strip():
        raise api_error(400, "bad_request", "问题不能为空")
    case.db_config_id = source.id
    case.question = body.question.strip()
    case.expected_sql = body.expected_sql.strip()
    case.baseline_json = baseline_json
    case.source_identity_json = _json(_source_identity(source))
    case.updated_at = datetime.utcnow()
    db.commit()
    return {"case": _serialize_case(case)}


@router.delete("/cases/{case_id}")
def delete_case(case_id: str, db: Session = Depends(get_db),
                user=Depends(security.get_current_user)):
    case = _case(db, case_id, user.id)
    db.query(EvaluationRun).filter(EvaluationRun.case_id == case.id).delete()
    db.delete(case)
    db.commit()
    return {"message": "评测样例已删除"}


@router.get("/cases/{case_id}/runs")
def list_case_runs(case_id: str, db: Session = Depends(get_db),
                   user=Depends(security.get_current_user)):
    _case(db, case_id, user.id)
    recover_pending_runs(db, case_id=case_id)
    runs = db.query(EvaluationRun).filter(EvaluationRun.case_id == case_id).order_by(
        EvaluationRun.created_at.desc(),
    ).limit(100).all()
    return {"runs": [_serialize_run(run) for run in runs]}


@router.post("/cases/{case_id}/run")
async def run_case(case_id: str, db: Session = Depends(get_db),
                   user=Depends(security.get_current_user)):
    case = _case(db, case_id, user.id)
    case_snapshot = _serialize_case(case)
    source = _source(db, case.db_config_id, user.id)
    question = case_snapshot["question"]
    expected_sql = case_snapshot["expected_sql"]
    source_config = _engine_config(source)
    current_source_identity = _source_identity(source)
    cfg = ai.get_ai_config(db)
    if not cfg["api_key"]:
        raise api_error(400, "bad_request", "请先配置 AI 服务")
    business_context = get_context(db, source.id, user.id)
    run = EvaluationRun(
        id=gen_uuid(), case_id=case.id, user_id=user.id,
        case_snapshot_json=_json(case_snapshot), status="pending", matched=False,
    )
    db.add(run)
    db.commit()
    engine = None
    generated_sql = None
    generation_snapshot = None
    actual = None
    changed = False
    error = None
    status = "error"
    matched = False
    try:
        engine = await asyncio.to_thread(engine_utils.build_engine, source_config)
        schema = await asyncio.to_thread(engine_utils.get_schema, engine)
        current_baseline = await asyncio.to_thread(_query, engine, expected_sql)
        old_baseline = case_snapshot["baseline"]
        ordered = bool(engine_utils.validate_query(expected_sql, engine.dialect.name).args.get("order"))
        changed = (not _same_result(old_baseline, current_baseline, ordered=ordered)
                   or case_snapshot["source_identity"] != current_source_identity)
        generation_snapshot = ai.sql_generation_snapshot(
            cfg, schema, question, dialect=engine.dialect.name,
            business_context=business_context,
        )
        run.generation_snapshot_json = _json(generation_snapshot)
        run.baseline_changed = changed
        db.commit()  # A provider request must never precede its durable inputs.
        generated_sql = await ai.generate_sql(cfg, schema, question,
                                              dialect=engine.dialect.name,
                                              business_context=business_context)
        after_ai_baseline = await asyncio.to_thread(_query, engine, expected_sql)
        if not _same_result(current_baseline, after_ai_baseline, ordered=ordered):
            changed = True
            error = "评测期间标准查询结果发生变化，无法确认通过或不匹配；请在数据稳定后重试"
        else:
            actual = await asyncio.to_thread(_query, engine, generated_sql)
            after_actual_baseline = await asyncio.to_thread(_query, engine, expected_sql)
            if not _same_result(after_ai_baseline, after_actual_baseline, ordered=ordered):
                changed = True
                error = "评测期间标准查询结果发生变化，无法确认通过或不匹配；请在数据稳定后重试"
            else:
                matched = _same_result(actual, after_actual_baseline, ordered=ordered)
                status = "passed" if matched else "mismatch"
    except asyncio.CancelledError:
        error = "评测请求已取消；生成输入已保留，请重新运行"
        raise
    except Exception as exc:  # A failed case is evidence, not a failed batch/request.
        error = str(exc)[:1000]
    finally:
        run.generated_sql = generated_sql
        run.actual_json = _json(actual) if actual is not None else None
        run.status = status
        run.matched = matched
        run.baseline_changed = changed
        run.error = error
        db.commit()
        if engine is not None:
            await asyncio.to_thread(engine.dispose)
    return {"run": _serialize_run(run)}
