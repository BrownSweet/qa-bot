"""A generation snapshot is bounded, credential-free, and additive on old workspaces."""
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect, text

from app import ai, quality_eval  # noqa: F401 - registers evaluation_runs
from app.config import settings
from app.database import prepare_schema
from app.workspace import CURRENT_SCHEMA_VERSION


def test_snapshot_matches_sql_context_limits_and_hides_legacy_url_credentials(monkeypatch):
    monkeypatch.setattr(settings, "DESKTOP_MODE", True)
    history = [{"role": "user", "content": f"old-{index}"} for index in range(8)]
    history[-1]["content"] = "x" * 3000
    history.append({"role": "system", "content": "must never reach SQL"})
    cfg = {
        "api_key": "secret-key", "model": "model-1",
        "api_url": "https://alice:password@example.test/secret-key/v1?token=secret-key#fragment",
    }
    snapshot = ai.sql_generation_snapshot(
        cfg, "sales(amount INTEGER)", "总额", dialect="postgresql",
        business_context="口" * 12001, history=history,
    )
    assert snapshot["model"] == "model-1"
    assert snapshot["api_url"] == "https://example.test"
    assert len(snapshot["api_url_sha256"]) == 64
    assert "password" not in str(snapshot) and "secret-key" not in str(snapshot)
    assert snapshot["history"] == ai._recent_history(history)
    assert len(snapshot["history"][-1]["content"]) == 2000
    assert snapshot["business_context"] == "口" * 12000
    assert snapshot["dialect"] == "postgresql"


def test_v4_database_migrates_snapshot_columns_without_rewriting_legacy_rows(tmp_path):
    assert CURRENT_SCHEMA_VERSION == 5
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.sqlite'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE messages (id VARCHAR(36) PRIMARY KEY, role VARCHAR(20), content TEXT)"))
            connection.execute(text("CREATE TABLE evaluation_runs (id VARCHAR(36) PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at VARCHAR(40) NOT NULL)"))
            connection.execute(text("INSERT INTO schema_migrations VALUES (4, 'legacy')"))
            connection.execute(text("INSERT INTO messages VALUES ('old-message', 'assistant', '旧回答')"))
            connection.execute(text("INSERT INTO evaluation_runs VALUES ('old-run')"))
        config = SimpleNamespace(DESKTOP_MODE=False)
        prepare_schema(engine, config)
        prepare_schema(engine, config)
        with engine.connect() as connection:
            assert "generation_snapshot_json" in {
                column["name"] for column in inspect(connection).get_columns("messages")
            }
            assert "generation_snapshot_json" in {
                column["name"] for column in inspect(connection).get_columns("evaluation_runs")
            }
            assert connection.execute(text(
                "SELECT content, generation_snapshot_json FROM messages WHERE id = 'old-message'"
            )).one() == ("旧回答", None)
            assert connection.execute(text(
                "SELECT generation_snapshot_json FROM evaluation_runs WHERE id = 'old-run'"
            )).scalar() is None
            assert connection.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar() == 5
            assert connection.execute(text("SELECT COUNT(*) FROM schema_migrations WHERE version = 5")).scalar() == 1
    finally:
        engine.dispose()
