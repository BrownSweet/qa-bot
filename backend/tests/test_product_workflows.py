"""Real local-source checks for reusable tasks, semantics, source editing, and golden SQL."""
import asyncio
import sqlite3
import time
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import analysis_tasks, models, quality_eval, schemas, semantic_context
from app import security
from app.database import Base, get_db
from app.routers import db_config, excel, system


@pytest.fixture
def product_db(tmp_path, monkeypatch):
    source_path = tmp_path / "source.sqlite"
    with sqlite3.connect(source_path) as connection:
        connection.execute("CREATE TABLE sales (amount INTEGER, region TEXT)")
        connection.executemany("INSERT INTO sales VALUES (?, ?)", [(10, "east"), (20, "west")])
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = models.User(id="owner", username="owner", phone="19900000001", password_hash="synthetic")
    other = models.User(id="other", username="other", phone="19900000002", password_hash="synthetic")
    db.add_all([user, other])
    db.flush()
    source = models.DBConfig(id="sales-source", user_id=user.id, name="Sales", type="sqlite",
                             file_path=str(source_path))
    db.add(source)
    db.commit()
    monkeypatch.setattr(db_config, "validate_data_file", lambda path, _kind: path)
    yield db, user, other, source, source_path
    db.close()
    engine.dispose()


def test_source_semantics_and_task_template_are_owner_scoped(product_db):
    db, user, other, source, _ = product_db
    saved = semantic_context.write_semantics(
        source.id, semantic_context.SemanticsWrite(context="销售额按付款时间统计，已退款订单排除"),
        db=db, user=user,
    )
    assert "付款时间" in saved["context"]
    assert semantic_context.get_context(db, source.id, other.id) == ""
    task = analysis_tasks.create_task(analysis_tasks.TaskWrite(
        name="按地区销售", question_template="查询{{region}}地区的销售额", db_config_id=source.id,
    ), db=db, user=user)["task"]
    prepared = analysis_tasks.prepare_run(task["id"], analysis_tasks.PrepareRun(
        parameters={"region": "华东"},
    ), db=db, user=user)
    assert prepared == {"question": "查询华东地区的销售额",
                        "db_config_id": source.id, "analysis_task_id": task["id"]}
    with pytest.raises(Exception):
        analysis_tasks.prepare_run(task["id"], analysis_tasks.PrepareRun(parameters={}),
                                   db=db, user=user)
    with pytest.raises(Exception):
        analysis_tasks.get_task(task["id"], db=db, user=other)
    with pytest.raises(Exception):
        analysis_tasks.create_task(analysis_tasks.TaskWrite(
            name="无效模板", question_template="查询{{参数}}销售额", db_config_id=source.id,
        ), db=db, user=user)


def test_changed_connection_requires_reconfirming_semantics_and_task(product_db, tmp_path):
    db, user, _other, source, _ = product_db
    semantic_context.write_semantics(source.id, semantic_context.SemanticsWrite(
        context="收入以已付款金额计算",
    ), db=db, user=user)
    task = analysis_tasks.create_task(analysis_tasks.TaskWrite(
        name="地区收入", question_template="查询{{region}}收入", db_config_id=source.id,
    ), db=db, user=user)["task"]
    assert not task["source_changed"]

    replacement = tmp_path / "replacement.sqlite"
    with sqlite3.connect(replacement) as connection:
        connection.execute("CREATE TABLE sales (amount INTEGER, region TEXT)")
    db_config.update_config(source.id, models_to_request(
        name="Sales", type="sqlite", file_path=str(replacement),
    ), db=db, user=user)
    saved = semantic_context.read_semantics(source.id, db=db, user=user)
    assert saved["stale"] is True and "已付款" in saved["context"]
    assert semantic_context.get_context(db, source.id, user.id) == ""
    listed = analysis_tasks.list_tasks(db=db, user=user)["tasks"]
    assert next(item for item in listed if item["id"] == task["id"])["source_changed"]
    with pytest.raises(Exception) as stale:
        analysis_tasks.prepare_run(task["id"], analysis_tasks.PrepareRun(
            parameters={"region": "华东"},
        ), db=db, user=user)
    assert stale.value.status_code == 409

    semantic_context.write_semantics(source.id, semantic_context.SemanticsWrite(
        context=saved["context"],
    ), db=db, user=user)
    assert semantic_context.get_context(db, source.id, user.id) == saved["context"]
    rebound = analysis_tasks.update_task(task["id"], analysis_tasks.TaskWrite(
        name="地区收入", question_template="查询{{region}}收入", db_config_id=source.id,
    ), db=db, user=user)["task"]
    assert not rebound["source_changed"]
    assert analysis_tasks.prepare_run(task["id"], analysis_tasks.PrepareRun(
        parameters={"region": "华东"},
    ), db=db, user=user)["question"] == "查询华东收入"


def test_edit_source_and_preview_real_rows(product_db):
    db, user, other, source, source_path = product_db
    updated = db_config.update_config(source.id, models_to_request(
        name="Updated", type="sqlite", file_path=str(source_path),
    ), db=db, user=user)["config"]
    assert updated["id"] == source.id
    assert updated["name"] == "Updated"
    schema = db_config.get_source_schema(source.id, db=db, user=user)
    assert schema["tables"][0]["name"] == "sales"
    preview = db_config.preview_source_table(source.id, table="sales", db=db, user=user)
    assert preview["columns"] == ["amount", "region"]
    assert preview["rows"] == [[10, "east"], [20, "west"]]
    with pytest.raises(Exception):
        db_config.preview_source_table(source.id, table="missing", db=db, user=user)
    with pytest.raises(Exception):
        db_config.get_source_schema(source.id, db=db, user=other)


def test_test_update_failure_preserves_entire_saved_source(product_db, monkeypatch):
    db, user, _other, source, source_path = product_db
    original = (source.name, source.type, source.host, source.port, source.database_name,
                source.username, source.password, source.file_path)
    candidate = models_to_request(name="Broken", type="mysql", host="unreachable.invalid",
                                  port=3306, database="sales", username="reader", password="new-secret")
    monkeypatch.setattr(db_config, "_probe_candidate", lambda _candidate: (_ for _ in ()).throw(OSError("offline")))
    result = asyncio.run(db_config.test_and_update_config(source.id, candidate, db=db, user=user))
    db.expire_all()
    assert result["success"] is False
    assert "原配置未修改" in result["message"]
    assert (source.name, source.type, source.host, source.port, source.database_name,
            source.username, source.password, source.file_path) == original
    assert source.file_path == str(source_path)


def test_test_update_reuses_saved_password_when_field_is_blank(product_db, monkeypatch):
    db, user, _other, source, _ = product_db
    source.type = "mysql"
    source.host = "old.example"
    source.port = 3306
    source.database_name = "sales"
    source.username = "reader"
    source.password = security.aes_encrypt("kept-secret")
    source.file_path = None
    db.commit()
    saved_password = source.password
    observed = []
    monkeypatch.setattr(db_config, "_probe_candidate", lambda candidate: observed.append(candidate.copy()))
    result = asyncio.run(db_config.test_and_update_config(source.id, models_to_request(
        name="Moved", type="mysql", host="new.example", port=3307,
        database="sales", username="reader", password="",
    ), db=db, user=user))
    assert result["success"] is True
    assert observed[0]["password"] == saved_password
    assert source.password == saved_password
    assert source.host == "new.example"
    assert source.port == 3307


def test_test_update_timeout_never_commits_candidate(product_db, monkeypatch):
    db, user, _other, source, source_path = product_db
    monkeypatch.setattr(db_config, "PROBE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(db_config, "_probe_candidate", lambda _candidate: time.sleep(0.05))
    result = asyncio.run(db_config.test_and_update_config(source.id, models_to_request(
        name="Late", type="sqlite", file_path=str(source_path),
    ), db=db, user=user))
    db.expire_all()
    assert result["success"] is False
    assert "原配置未修改" in result["message"]
    assert source.name == "Sales"


def test_deleting_source_unbinds_session_but_keeps_message_history(product_db):
    db, user, _other, source, _ = product_db
    session = models.ChatSession(id="conversation", user_id=user.id, name="历史分析",
                                 db_config_id=source.id)
    message = models.Message(id="prior-answer", session_id=session.id, user_id=user.id,
                             role="assistant", content="历史结论", status="completed",
                             db_config_id=source.id, source_snapshot_json='{"name":"Sales"}')
    db.add_all([session, message])
    db.commit()
    db_config.delete_config(source.id, db=db, user=user)
    db.expire_all()
    assert db.get(models.ChatSession, session.id).db_config_id is None
    assert db.get(models.Message, message.id).source_snapshot_json == '{"name":"Sales"}'


def test_legacy_excel_preview_streams_with_row_and_byte_limits(product_db, tmp_path, monkeypatch):
    from openpyxl import Workbook

    _db, user, _other, _source, _ = product_db
    path = tmp_path / "sample.xlsx"
    workbook = Workbook()
    workbook.active.title = "Sheet1"
    workbook.active.append(["payload", "amount"])
    for index in range(320):
        workbook.active.append(["中" * 6000 if index == 0 else f"r{index}", index])
    workbook.save(path)
    monkeypatch.setattr(excel, "validate_data_file", lambda value, _kind: value)
    result = excel.sheet_data(schemas.GetSheetDataRequest(
        file_path=str(path), sheet_name="Sheet1", limit=10,
    ), user=user)
    assert result["total_rows"] == 320
    assert len(result["rows"]) == 10
    assert result["coverage"]["has_more"] is True
    assert result["coverage"]["cells_truncated"] is True
    assert len(result["rows"][0][0].encode("utf-8")) <= 8192
    with pytest.raises(Exception):
        schemas.GetSheetDataRequest(file_path=str(path), sheet_name="Sheet1", limit=10_000)


def test_web_user_can_read_setup_state_without_admin_config_access(product_db):
    db, user, _other, source, _path = product_db
    readiness = system.get_readiness(db=db, user=user)
    assert readiness["db_config_count"] == 1
    assert readiness["can_manage_ai"] is False
    assert readiness["ai_ready"] is False
    assert readiness["model"]


def models_to_request(**overrides):
    from app.schemas import DBConfigRequest
    return DBConfigRequest(**overrides)


def test_golden_case_checks_live_result_and_detects_baseline_change(product_db, monkeypatch):
    db, user, _other, source, source_path = product_db
    case = quality_eval.create_case(quality_eval.CaseWrite(
        db_config_id=source.id,
        question="销售总额是多少？",
        expected_sql="SELECT SUM(amount) FROM sales",
    ), db=db, user=user)["case"]
    assert case["baseline"]["rows"] == [[30]]

    monkeypatch.setattr(quality_eval.ai, "get_ai_config", lambda _db: {"api_key": "synthetic"})

    async def good_sql(*_args, **_kwargs):
        return "SELECT SUM(amount) FROM sales"

    monkeypatch.setattr(quality_eval.ai, "generate_sql", good_sql)
    first = asyncio.run(quality_eval.run_case(case["id"], db=db, user=user))["run"]
    assert first["status"] == "passed" and first["matched"]
    assert first["baseline_changed"] is False
    assert first["generation_snapshot"]["question"] == "销售总额是多少？"
    assert "sales" in first["generation_snapshot"]["schema"]
    assert first["generation_snapshot"]["history"] == []
    assert first["generation_snapshot"]["task"] is None

    with sqlite3.connect(source_path) as connection:
        connection.execute("INSERT INTO sales VALUES (5, 'east')")
    second = asyncio.run(quality_eval.run_case(case["id"], db=db, user=user))["run"]
    assert second["status"] == "passed" and second["baseline_changed"]

    async def wrong_sql(*_args, **_kwargs):
        return "SELECT COUNT(*) FROM sales"

    monkeypatch.setattr(quality_eval.ai, "generate_sql", wrong_sql)
    third = asyncio.run(quality_eval.run_case(case["id"], db=db, user=user))["run"]
    assert third["status"] == "mismatch" and not third["matched"]
    assert len(quality_eval.list_case_runs(case["id"], db=db, user=user)["runs"]) == 3


def test_evaluation_does_not_mark_identical_sql_wrong_when_data_changes_during_ai(product_db, monkeypatch):
    db, user, _other, source, source_path = product_db
    sql = "SELECT SUM(amount) AS total FROM sales"
    case = quality_eval.create_case(quality_eval.CaseWrite(
        db_config_id=source.id, question="销售总额是多少？", expected_sql=sql,
    ), db=db, user=user)["case"]
    monkeypatch.setattr(quality_eval.ai, "get_ai_config", lambda _db: {"api_key": "synthetic"})

    async def same_sql_after_new_sale(*_args, **_kwargs):
        with sqlite3.connect(source_path) as connection:
            connection.execute("INSERT INTO sales VALUES (5, 'east')")
        return sql

    monkeypatch.setattr(quality_eval.ai, "generate_sql", same_sql_after_new_sale)
    run = asyncio.run(quality_eval.run_case(case["id"], db=db, user=user))["run"]
    assert run["generated_sql"] == sql
    assert run["status"] == "error" and run["matched"] is False
    assert run["baseline_changed"] is True
    assert "评测期间标准查询结果发生变化" in run["error"]
    assert run["actual"] is None
    assert quality_eval.list_case_runs(case["id"], db=db, user=user)["runs"][0]["status"] == "error"


def test_evaluation_marks_data_change_after_ai_query_inconclusive(product_db, monkeypatch):
    db, user, _other, source, source_path = product_db
    sql = "SELECT SUM(amount) AS total FROM sales"
    generated_sql = "SELECT SUM(amount) AS total FROM sales WHERE amount >= 0"
    case = quality_eval.create_case(quality_eval.CaseWrite(
        db_config_id=source.id, question="销售总额是多少？", expected_sql=sql,
    ), db=db, user=user)["case"]
    monkeypatch.setattr(quality_eval.ai, "get_ai_config", lambda _db: {"api_key": "synthetic"})

    async def generate_sql(*_args, **_kwargs):
        return generated_sql

    monkeypatch.setattr(quality_eval.ai, "generate_sql", generate_sql)
    original_query = quality_eval._query

    def query_with_new_sale_after_actual(engine, query_sql):
        result = original_query(engine, query_sql)
        if query_sql == generated_sql:
            with sqlite3.connect(source_path) as connection:
                connection.execute("INSERT INTO sales VALUES (5, 'east')")
        return result

    monkeypatch.setattr(quality_eval, "_query", query_with_new_sale_after_actual)
    run = asyncio.run(quality_eval.run_case(case["id"], db=db, user=user))["run"]
    assert run["actual"]["rows"] == [[30]]
    assert run["status"] == "error" and run["matched"] is False
    assert run["baseline_changed"] is True
    assert "评测期间标准查询结果发生变化" in run["error"]


def test_evaluation_history_keeps_the_original_golden_after_case_edit(product_db, monkeypatch):
    db, user, _other, source, _source_path = product_db
    original = quality_eval.create_case(quality_eval.CaseWrite(
        db_config_id=source.id, question="销售总额是多少？",
        expected_sql="SELECT SUM(amount) FROM sales",
    ), db=db, user=user)["case"]
    monkeypatch.setattr(quality_eval.ai, "get_ai_config", lambda _db: {"api_key": "synthetic"})

    async def generated_sql(*_args, **_kwargs):
        return "SELECT SUM(amount) FROM sales"

    monkeypatch.setattr(quality_eval.ai, "generate_sql", generated_sql)
    run = asyncio.run(quality_eval.run_case(original["id"], db=db, user=user))["run"]
    assert run["status"] == "passed"
    quality_eval.update_case(original["id"], quality_eval.CaseWrite(
        db_config_id=source.id, question="共有几笔销售？",
        expected_sql="SELECT COUNT(*) FROM sales",
    ), db=db, user=user)
    saved = quality_eval.list_case_runs(original["id"], db=db, user=user)["runs"][0]
    assert saved["case_snapshot"]["question"] == "销售总额是多少？"
    assert saved["case_snapshot"]["expected_sql"] == "SELECT SUM(amount) FROM sales"
    assert saved["case_snapshot"]["baseline"]["rows"] == [[30]]
    assert saved["generation_snapshot"]["question"] == "销售总额是多少？"
    assert quality_eval.list_cases(db=db, user=user)["cases"][0]["question"] == "共有几笔销售？"


def test_failed_evaluation_keeps_generation_inputs_without_provider_key(product_db, monkeypatch):
    db, user, _other, source, _ = product_db
    case = quality_eval.create_case(quality_eval.CaseWrite(
        db_config_id=source.id, question="销售总额是多少？",
        expected_sql="SELECT SUM(amount) FROM sales",
    ), db=db, user=user)["case"]
    semantic_context.write_semantics(source.id, semantic_context.SemanticsWrite(
        context="只看已支付销售",
    ), db=db, user=user)
    monkeypatch.setattr(quality_eval.ai, "get_ai_config", lambda _db: {
        "api_key": "synthetic-secret", "api_url": "https://internal.example/v1",
        "model": "eval-model",
    })

    async def failed_sql(*_args, **_kwargs):
        raise RuntimeError("synthetic provider failure")

    monkeypatch.setattr(quality_eval.ai, "generate_sql", failed_sql)
    run = asyncio.run(quality_eval.run_case(case["id"], db=db, user=user))["run"]
    assert run["status"] == "error"
    assert run["case_snapshot"]["expected_sql"] == "SELECT SUM(amount) FROM sales"
    snapshot = run["generation_snapshot"]
    assert snapshot["model"] == "eval-model"
    assert snapshot["business_context"] == "只看已支付销售"
    assert snapshot["question"] == case["question"]
    assert snapshot["history"] == [] and snapshot["task"] is None
    assert "synthetic-secret" not in str(snapshot)
    assert quality_eval.list_case_runs(case["id"], db=db, user=user)["runs"][0]["generation_snapshot"] == snapshot


def test_evaluation_commits_pending_snapshot_before_provider_and_marks_cancellation(product_db, monkeypatch):
    db, user, _other, source, _ = product_db
    case = quality_eval.create_case(quality_eval.CaseWrite(
        db_config_id=source.id, question="销售总额是多少？",
        expected_sql="SELECT SUM(amount) FROM sales",
    ), db=db, user=user)["case"]
    monkeypatch.setattr(quality_eval.ai, "get_ai_config", lambda _db: {
        "api_key": "synthetic-key", "api_url": "https://proxy.example.com/tenant-secret-token/v1",
        "model": "eval-model",
    })

    async def cancelled_provider(*_args, **_kwargs):
        # An independent connection must already observe the pending run and
        # its inputs before a request is sent to an AI provider.
        with sessionmaker(bind=db.bind)() as fresh:
            pending = fresh.query(quality_eval.EvaluationRun).one()
            assert pending.status == "pending"
            assert pending.generation_snapshot_json is not None
            assert "tenant-secret-token" not in pending.generation_snapshot_json
        raise asyncio.CancelledError()

    monkeypatch.setattr(quality_eval.ai, "generate_sql", cancelled_provider)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(quality_eval.run_case(case["id"], db=db, user=user))
    saved = quality_eval.list_case_runs(case["id"], db=db, user=user)["runs"][0]
    assert saved["status"] == "error" and saved["matched"] is False
    assert "取消" in saved["error"]
    assert saved["generation_snapshot"]["question"] == case["question"]


def test_stale_web_pending_evaluation_recovers_but_recent_run_stays_pending(product_db):
    db, user, _other, source, _ = product_db
    case = quality_eval.create_case(quality_eval.CaseWrite(
        db_config_id=source.id, question="销售总额是多少？",
        expected_sql="SELECT SUM(amount) FROM sales",
    ), db=db, user=user)["case"]
    db.add_all([
        quality_eval.EvaluationRun(id="stale-run", case_id=case["id"], user_id=user.id,
                                   status="pending", matched=False,
                                   generation_snapshot_json='{"question":"旧问题"}',
                                   created_at=datetime.utcnow() - timedelta(minutes=31)),
        quality_eval.EvaluationRun(id="recent-run", case_id=case["id"], user_id=user.id,
                                   status="pending", matched=False),
    ])
    db.commit()
    runs = {run["id"]: run for run in quality_eval.list_case_runs(case["id"], db=db, user=user)["runs"]}
    assert runs["stale-run"]["status"] == "error"
    assert "重新运行" in runs["stale-run"]["error"]
    assert runs["stale-run"]["generation_snapshot"]["question"] == "旧问题"
    assert runs["recent-run"]["status"] == "pending"
    assert quality_eval.recover_pending_runs(db, include_recent=True) == 1
    assert db.get(quality_eval.EvaluationRun, "recent-run").status == "error"


def test_golden_comparison_rejects_swapped_labels_and_wrong_ranking(product_db, monkeypatch):
    db, user, _other, source, _ = product_db
    monkeypatch.setattr(quality_eval.ai, "get_ai_config", lambda _db: {"api_key": "synthetic"})
    cases = [
        quality_eval.create_case(quality_eval.CaseWrite(
            db_config_id=source.id, question="收入和订单数",
            expected_sql="SELECT amount AS revenue, amount + 1 AS orders FROM sales WHERE amount = 10",
        ), db=db, user=user)["case"],
        quality_eval.create_case(quality_eval.CaseWrite(
            db_config_id=source.id, question="按金额升序排名",
            expected_sql="SELECT amount FROM sales ORDER BY amount ASC",
        ), db=db, user=user)["case"],
    ]

    async def wrong_sql(_cfg, _schema, question, **_kwargs):
        if question == "收入和订单数":
            return "SELECT amount AS orders, amount + 1 AS revenue FROM sales WHERE amount = 10"
        return "SELECT amount FROM sales ORDER BY amount DESC"

    monkeypatch.setattr(quality_eval.ai, "generate_sql", wrong_sql)
    for case in cases:
        run = asyncio.run(quality_eval.run_case(case["id"], db=db, user=user))["run"]
        assert run["status"] == "mismatch" and not run["matched"]


def test_golden_batch_import_is_atomic(product_db):
    db, user, _other, source, _path = product_db
    valid = quality_eval.CaseWrite(
        db_config_id=source.id, question="共有几笔？",
        expected_sql="SELECT COUNT(*) FROM sales",
    )
    invalid = quality_eval.CaseWrite(
        db_config_id=source.id, question="错误标准", expected_sql="DELETE FROM sales",
    )
    with pytest.raises(Exception):
        quality_eval.import_cases(quality_eval.CaseImport(cases=[valid, invalid]),
                                  db=db, user=user)
    assert quality_eval.list_cases(db=db, user=user)["cases"] == []
    imported = quality_eval.import_cases(quality_eval.CaseImport(cases=[valid]),
                                         db=db, user=user)
    assert imported["cases"][0]["baseline"]["rows"] == [[2]]


def test_golden_case_rejects_truncated_cell_as_standard_answer(product_db):
    db, user, _other, source, source_path = product_db
    with sqlite3.connect(source_path) as connection:
        connection.execute("ALTER TABLE sales ADD COLUMN payload TEXT")
        connection.execute("UPDATE sales SET payload = ? WHERE amount = 10", ("x" * 10000,))
    with pytest.raises(Exception) as rejected:
        quality_eval.create_case(quality_eval.CaseWrite(
            db_config_id=source.id, question="原始内容是什么？",
            expected_sql="SELECT payload FROM sales WHERE amount = 10",
        ), db=db, user=user)
    assert rejected.value.status_code == 400
    assert quality_eval.list_cases(db=db, user=user)["cases"] == []


def test_public_api_routes_reach_real_product_workflows(product_db):
    from main import app
    db, user, _other, source, _path = product_db
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[security.get_current_user] = lambda: user
    try:
        with TestClient(app) as client:
            assert client.get("/api/system/readiness").json()["db_config_count"] == 1
            created = client.post("/api/analysis-tasks", json={
                "name": "销售", "question_template": "统计{{region}}销售额",
                "db_config_id": source.id,
            })
            assert created.status_code == 201
            task_id = created.json()["task"]["id"]
            prepared = client.post(f"/api/analysis-tasks/{task_id}/prepare", json={
                "parameters": {"region": "东区"},
            })
            assert prepared.status_code == 200
            assert prepared.json()["question"] == "统计东区销售额"
            schema = client.get(f"/api/db/configs/{source.id}/schema")
            assert schema.status_code == 200
            assert schema.json()["tables"][0]["name"] == "sales"
            tested_update = client.post(f"/api/db/configs/{source.id}/test-update", json={
                "name": "Verified sales", "type": "sqlite", "file_path": str(_path),
            })
            assert tested_update.status_code == 200
            assert tested_update.json()["success"] is True
            assert tested_update.json()["config"]["name"] == "Verified sales"
            golden = client.post("/api/evaluation/cases", json={
                "db_config_id": source.id, "question": "销售额是多少？",
                "expected_sql": "SELECT SUM(amount) FROM sales",
            })
            assert golden.status_code == 201
            assert golden.json()["case"]["baseline"]["rows"] == [[30]]
    finally:
        app.dependency_overrides.clear()
