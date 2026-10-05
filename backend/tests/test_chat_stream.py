"""Cancellation and context tests do not start the app or call external services."""
import asyncio
import json
import os
import threading
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import ai, models, schemas, semantic_context
from app.database import Base
from app.routers import chat


@pytest.fixture
def conversation(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    user = models.User(id="owner", username="owner", phone="19900000001", password_hash="synthetic")
    other = models.User(id="other", username="other", phone="19900000002", password_hash="synthetic")
    db.add_all([user, other])
    db.flush()
    db.add_all([
        models.ChatSession(id="session", user_id=user.id, name="current"),
        models.ChatSession(id="unrelated", user_id=other.id, name="other"),
        models.DBConfig(id="source", user_id=user.id, name="synthetic", type="sqlite", file_path="synthetic.sqlite"),
    ])
    db.flush()
    db.add_all([
        models.Message(id="previous", session_id="session", user_id=user.id, role="user",
                       content="previous question", status="completed", db_config_id="source",
                       source_snapshot_json=json.dumps({"type": "sqlite", "file_path": "synthetic.sqlite"})),
        models.Message(id="unrelated-message", session_id="unrelated", user_id=other.id, role="assistant", content="private other history", status="completed"),
        models.Message(id="old-pending", session_id="session", user_id=user.id, role="assistant", content="pending text", status="pending"),
    ])
    db.commit()
    recorded = {"disposed": False, "threads": [], "finished": [], "evidence": [], "snapshots": []}
    target = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    target.dispose = lambda: recorded.update(disposed=True)
    main_thread = threading.get_ident()
    def build(_config):
        recorded["threads"].append(threading.get_ident())
        return target
    def schema(_engine):
        recorded["threads"].append(threading.get_ident())
        return "items(amount INTEGER)"
    def query(_engine, _sql):
        recorded["threads"].append(threading.get_ident())
        return ["amount"], [[3]], {"row_limit": 200, "returned_rows": 1, "has_more": False}
    async def generate_sql(_cfg, _schema, _question, **kwargs):
        recorded["sql_context"] = kwargs
        return "SELECT amount FROM items"
    async def answer(*_args, **kwargs):
        recorded["analysis_args"] = _args
        recorded["analysis_context"] = kwargs
        try:
            yield "partial answer"
            await asyncio.sleep(0)
            yield " completed"
        finally:
            recorded["stream_closed"] = True
    def finish(*args):
        recorded["finished"].append(args)
    monkeypatch.setattr(chat, "validate_data_file", lambda path, kind: path)
    monkeypatch.setattr(chat.engine_utils, "build_engine", build)
    monkeypatch.setattr(chat.engine_utils, "get_schema", schema)
    monkeypatch.setattr(chat.engine_utils, "run_query_with_metadata", query)
    monkeypatch.setattr(chat.ai, "get_ai_config", lambda _db: {"api_key": "synthetic"})
    monkeypatch.setattr(chat.ai, "generate_sql", generate_sql)
    monkeypatch.setattr(chat.ai, "analyze_stream", answer)
    monkeypatch.setattr(chat, "_finish", finish)
    monkeypatch.setattr(chat, "_record_evidence", lambda *args: recorded["evidence"].append(args))
    monkeypatch.setattr(chat, "_record_generation_snapshot",
                        lambda *args: recorded["snapshots"].append(args))
    body = schemas.SendMessageRequest(session_id="session", db_config_id="source", question="this question")
    yield db, user, body, recorded, main_thread
    db.close()
    engine.dispose()


def test_completed_stream_uses_scoped_history_and_worker_threads(conversation):
    db, user, body, recorded, main_thread = conversation
    response = chat.send_message(body, db=db, user=user)
    async def consume():
        return [event async for event in response.body_iterator]
    events = asyncio.run(consume())
    assert recorded["finished"][0][1:] == ("partial answer completed", "completed")
    assert recorded["disposed"]
    assert all(thread != main_thread for thread in recorded["threads"])
    assert recorded["sql_context"] == {
        "dialect": "sqlite", "history": [{"role": "user", "content": "previous question"}],
        "business_context": "",
    }
    assert recorded["evidence"][0][1]["sql"] == "SELECT amount FROM items"
    assert recorded["snapshots"][0][1]["history"] == [
        {"role": "user", "content": "previous question"},
    ]
    assert recorded["snapshots"][0][1]["schema"] == "items(amount INTEGER)"
    assert '"status": "completed"' in events[-1]
    assert '"generation_snapshot"' in events[-1]


def test_edited_source_location_does_not_reuse_old_context(conversation):
    db, user, body, recorded, _ = conversation
    db.get(models.DBConfig, "source").file_path = "different.sqlite"
    db.commit()
    response = chat.send_message(body, db=db, user=user)

    async def consume():
        return [event async for event in response.body_iterator]

    asyncio.run(consume())
    assert recorded["sql_context"]["history"] == []


@pytest.mark.parametrize("close_method", ["cancel", "close"])
def test_cancelled_stream_keeps_partial_text_and_disposes(conversation, close_method):
    db, user, body, recorded, _ = conversation
    response = chat.send_message(body, db=db, user=user)
    async def cancel():
        stream = response.body_iterator
        while "partial answer" not in await anext(stream):
            pass
        if close_method == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await stream.athrow(asyncio.CancelledError())
        else:
            await stream.aclose()
    asyncio.run(cancel())
    assert recorded["finished"][0][1:] == ("partial answer", "cancelled")
    assert recorded["disposed"]
    assert recorded["stream_closed"]


def test_error_keeps_partial_text(conversation, monkeypatch):
    db, user, body, recorded, _ = conversation
    async def interrupted(*args, **kwargs):
        yield "first part"
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(chat.ai, "analyze_stream", interrupted)
    response = chat.send_message(body, db=db, user=user)
    async def consume():
        return [event async for event in response.body_iterator]
    asyncio.run(consume())
    assert recorded["finished"][0][1].startswith("first part\n\n")
    assert recorded["finished"][0][2] == "error"
    assert recorded["disposed"]


def test_cancelled_worker_finishes_before_disposal():
    started = threading.Event()
    release = threading.Event()
    recorded = []
    def work():
        started.set()
        release.wait(2)
        recorded.append("work complete")
        return object()
    async def cancel():
        task = asyncio.create_task(chat._blocking(work, on_cancel=lambda _: recorded.append("disposed")))
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(cancel())
    assert recorded == ["work complete", "disposed"]


def test_truncation_notice_is_persisted(conversation, monkeypatch):
    db, user, body, recorded, _ = conversation
    monkeypatch.setattr(chat.engine_utils, "run_query_with_metadata", lambda *_: (
        ["amount"], [[i] for i in range(200)], {"row_limit": 200, "returned_rows": 200, "has_more": True},
    ))
    response = chat.send_message(body, db=db, user=user)
    async def consume():
        return [event async for event in response.body_iterator]
    events = asyncio.run(consume())
    assert "前 50 行" in recorded["finished"][0][1]
    result = next(event for event in events if event.startswith("event: result"))
    assert '"analysis_truncated": true' in result


def test_evidence_uses_actual_limited_sql(conversation, monkeypatch):
    db, user, body, recorded, _ = conversation
    monkeypatch.setattr(chat.engine_utils, "run_query_with_metadata", lambda *_: (
        ["amount"], [[3]], {"row_limit": 200, "returned_rows": 1,
                             "has_more": False, "executed_sql": "SELECT amount FROM items LIMIT 201"},
    ))
    response = chat.send_message(body, db=db, user=user)

    async def consume():
        return [event async for event in response.body_iterator]

    asyncio.run(consume())
    evidence = recorded["evidence"][0][1]
    assert evidence["sql"] == "SELECT amount FROM items LIMIT 201"
    assert evidence["generated_sql"] == "SELECT amount FROM items"
    assert "executed_sql" not in evidence["coverage"]
    assert recorded["analysis_args"][2] == "SELECT amount FROM items LIMIT 201"


def test_cell_truncation_is_visible_in_saved_answer(conversation, monkeypatch):
    db, user, body, recorded, _ = conversation
    monkeypatch.setattr(chat.engine_utils, "run_query_with_metadata", lambda *_: (
        ["payload"], [["预览"]],
        {"row_limit": 200, "returned_rows": 1, "has_more": False,
         "cells_truncated": True, "truncated_cell_count": 1, "result_truncated": False},
    ))
    response = chat.send_message(body, db=db, user=user)

    async def consume():
        return [event async for event in response.body_iterator]

    asyncio.run(consume())
    evidence = recorded["evidence"][0][1]
    assert evidence["coverage"]["analysis_truncated"] is True
    assert evidence["coverage"]["evidence_truncated"] is True
    assert "1 个单元格内容已截断" in recorded["finished"][0][1]


def test_oversized_first_analysis_row_keeps_evidence_and_reports_error(conversation, monkeypatch):
    db, user, body, recorded, _ = conversation
    monkeypatch.setattr(chat.engine_utils, "run_query_with_metadata", lambda *_: (
        ["payload"], [["x" * (ai.MAX_ANALYSIS_RESULT_BYTES + 100)]],
        {"row_limit": 200, "returned_rows": 1, "has_more": False},
    ))
    response = chat.send_message(body, db=db, user=user)

    async def consume():
        return [event async for event in response.body_iterator]

    events = asyncio.run(consume())
    assert recorded["evidence"]
    assert recorded["evidence"][0][1]["coverage"]["analyzed_rows"] == 0
    assert recorded["finished"][0][2] == "error"
    assert "单行查询结果过宽" in recorded["finished"][0][1]
    assert '"status": "error"' in events[-1]


def test_ai_payload_carries_dialect_context_and_coverage(monkeypatch):
    from contextlib import asynccontextmanager
    requests = []
    class Reply:
        def raise_for_status(self):
            return None
        def json(self):
            return {"choices": [{"message": {"content": "SELECT 1"}}]}
        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"sample analysis"}}]}'
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
            yield 'data: [DONE]'
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            requests.append(kwargs["json"])
            return Reply()
        @asynccontextmanager
        async def stream(self, method, url, **kwargs):
            requests.append(kwargs["json"])
            yield Reply()
    monkeypatch.setattr(ai.httpx, "AsyncClient", Client)
    cfg = {"api_key": "synthetic", "api_url": "https://synthetic.invalid", "timeout": 1}
    history = [{"role": "user", "content": f"question {i}"} for i in range(10)]
    async def request():
        assert await ai.generate_sql(cfg, "items(amount INTEGER)", "total", dialect="mysql", history=history) == "SELECT 1"
        chunks = [chunk async for chunk in ai.analyze_stream(
            cfg, "total", "SELECT amount FROM items", ["amount"], [[i] for i in range(200)],
            metadata={"row_limit": 200, "returned_rows": 200, "has_more": True}, history=history,
        )]
        assert chunks == ["sample analysis"]
    asyncio.run(request())
    assert "目标数据库方言: mysql" in requests[0]["messages"][-1]["content"]
    assert [item["content"] for item in requests[0]["messages"][1:-1]] == [f"question {i}" for i in range(4, 10)]
    prompt = requests[1]["messages"][-1]["content"]
    result_json = prompt.split("查询结果(JSON):\n", 1)[1].split("\n\n", 1)[0]
    result = json.loads(result_json)
    assert len(result["rows"]) == 50
    assert result["coverage"]["analysis_truncated"] is True
    assert result["coverage"]["returned_rows"] == 200


def test_analysis_input_has_independent_byte_budget():
    rows = [["x" * 8000] for _ in range(50)]
    result_json, coverage = ai.prepare_analysis_result(
        ["payload"], rows, {"row_limit": 200, "returned_rows": 50,
                            "has_more": False, "cells_truncated": False},
    )
    assert len(result_json.encode("utf-8")) <= ai.MAX_ANALYSIS_RESULT_BYTES
    assert 0 < coverage["analyzed_rows"] < 50
    assert coverage["analysis_bytes_truncated"] is True
    assert coverage["analysis_truncated"] is True
    assert len(json.loads(result_json)["rows"]) == coverage["analyzed_rows"]


@pytest.mark.parametrize("frames, expected", [
    ([
        'data: {"choices":[{"delta":{"content":"partial"}}]}',
    ], "结束标记"),
    ([
        'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}',
        'data: [DONE]',
    ], "未完整生成"),
    ([
        'data: {"error":{"message":"upstream failed"}}',
        'data: [DONE]',
    ], "upstream failed"),
    ([
        'data: {"choices":[{"delta":{"content":"partial"}}]}',
        'data: [DONE]',
    ], "结束标记"),
])
def test_ai_stream_rejects_incomplete_or_error_frames(monkeypatch, frames, expected):
    from contextlib import asynccontextmanager

    class Reply:
        def raise_for_status(self):
            pass
        async def aiter_lines(self):
            for line in frames:
                yield line

    class Client:
        def __init__(self, **_kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            pass
        @asynccontextmanager
        async def stream(self, *_args, **_kwargs):
            yield Reply()

    monkeypatch.setattr(ai.httpx, "AsyncClient", Client)
    cfg = {"api_key": "synthetic", "api_url": "https://synthetic.invalid/v1", "timeout": 1}

    async def collect():
        return [part async for part in ai.analyze_stream(cfg, "q", "SELECT 1", ["one"], [[1]])]

    with pytest.raises(RuntimeError, match=expected):
        asyncio.run(collect())


def test_completion_url_accepts_base_with_or_without_v1():
    assert ai._completion_url({"api_url": "https://api.deepseek.com"}) == \
        "https://api.deepseek.com/v1/chat/completions"
    assert ai._completion_url({"api_url": "https://api.deepseek.com/v1/"}) == \
        "https://api.deepseek.com/v1/chat/completions"
