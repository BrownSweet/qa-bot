"""Safety regressions use only synthetic in-memory databases and temporary files."""
import os
from datetime import datetime

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from app import engine_utils


@pytest.mark.parametrize("dialect,sql", [
    ("sqlite", "WITH marker AS (SELECT 1) DELETE FROM items RETURNING id"),
    ("postgresql", "WITH erased AS (DELETE FROM items RETURNING id) SELECT * FROM erased"),
    ("sqlite", "SELECT 1; DELETE FROM items"),
    ("sqlite", "DELETE FROM items"),
    ("sqlite", "PRAGMA query_only = OFF"),
    ("sqlite", "ATTACH DATABASE '/tmp/other.db' AS other"),
    ("postgresql", "SELECT * INTO copied FROM items"),
    ("postgresql", "SELECT * FROM items FOR UPDATE"),
    ("mysql", "SELECT * FROM items LOCK IN SHARE MODE"),
    ("mysql", "SELECT * FROM items INTO OUTFILE '/tmp/data'"),
    ("sqlite", "SELECT load_extension('anything')"),
    ("sqlite", "SELECT writefile('/tmp/data', 'text')"),
    ("postgresql", "SELECT pg_read_file('/etc/passwd')"),
    ("postgresql", "SELECT pg_sleep(30)"),
    ("postgresql", "SELECT set_config('default_transaction_read_only', 'off', false)"),
    ("postgresql", "SELECT nextval('seq')"),
    ("mysql", "SELECT SLEEP(30)"),
    ("mysql", "SELECT GET_LOCK('lock', 30)"),
    ("mysql", "SELECT @value := 1"),
    ("mysql", "SELECT /*+ MAX_EXECUTION_TIME(0) */ * FROM items"),
    ("postgresql", "SELECT custom_write_function()"),
    ("postgresql", "SELECT custom_schema.abs(1)"),
])
def test_rejects_side_effects_recursively(dialect, sql):
    with pytest.raises(ValueError):
        engine_utils.validate_query(sql, dialect)


@pytest.fixture
def source():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE items (id INTEGER, amount REAL)")
        conn.exec_driver_sql("INSERT INTO items VALUES (1, 3), (2, 20), (3, 100)")
    yield engine
    engine.dispose()


def test_readonly_cte_aggregate_and_truncation(source):
    columns, rows, metadata = engine_utils.run_query_with_metadata(
        source, "WITH chosen AS (SELECT amount FROM items) SELECT amount FROM chosen ORDER BY amount", limit=2,
    )
    assert columns == ["amount"]
    assert rows == [[3.0], [20.0]]
    assert {key: metadata[key] for key in ("row_limit", "returned_rows", "has_more")} == {
        "row_limit": 2, "returned_rows": 2, "has_more": True,
    }
    assert metadata["executed_sql"].endswith("LIMIT 3")
    assert engine_utils.run_query(source, "SELECT MAX(amount), SUM(amount) FROM items")[1] == [[100.0, 123.0]]


def test_explicit_smaller_limit_preserved(source):
    _, rows, metadata = engine_utils.run_query_with_metadata(source, "SELECT id FROM items ORDER BY id LIMIT 1", 2)
    assert rows == [[1]]
    assert metadata["has_more"] is False


def test_large_text_blob_and_total_result_budget_are_explicit(source):
    with source.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE big_values (id INTEGER, payload TEXT, blob_value BLOB)")
        conn.exec_driver_sql("INSERT INTO big_values VALUES (?, ?, ?)",
                             (1, "中" * 40000, b"x" * 1000000))
        conn.exec_driver_sql("INSERT INTO big_values VALUES (?, ?, ?)",
                             (2, "ordinary", b"ok"))
    _, rows, metadata = engine_utils.run_query_with_metadata(
        source, "SELECT payload, blob_value FROM big_values ORDER BY id",
    )
    assert len(rows) == 2
    assert rows[0][0].endswith("字节）")
    assert rows[0][1].startswith("0x")
    assert rows[0][1].endswith("字节）")
    assert metadata["cells_truncated"] is True
    assert metadata["truncated_cell_count"] == 2
    assert metadata["result_truncated"] is False
    assert metadata["cell_byte_limit"] == 8192
    assert metadata["result_byte_limit"] == 262144
    for row in rows:
        for cell in row:
            assert engine_utils._json_size(cell) <= metadata["cell_byte_limit"]
    assert engine_utils._json_size(rows) == metadata["result_bytes"]


def test_row_budget_stops_fetch_and_marks_omitted_rows(source, monkeypatch):
    with source.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE many_values (id INTEGER, payload TEXT)")
        conn.exec_driver_sql("INSERT INTO many_values VALUES (?, ?)",
                             [(index, "x" * 100) for index in range(10)])
    monkeypatch.setattr(engine_utils, "MAX_RESULT_BYTES", 300)
    _, rows, metadata = engine_utils.run_query_with_metadata(
        source, "SELECT payload FROM many_values ORDER BY id",
    )
    assert len(rows) == 2
    assert metadata["returned_rows"] == 2
    assert metadata["has_more"] is True
    assert metadata["result_truncated"] is True
    assert metadata["cells_truncated"] is False
    assert metadata["result_bytes"] <= metadata["result_byte_limit"] == 300


def test_schema_budget_fails_without_sending_partial_structure(source, monkeypatch):
    monkeypatch.setattr(engine_utils, "MAX_SCHEMA_BYTES", 10)
    with pytest.raises(ValueError, match="数据表结构超过"):
        engine_utils.get_schema(source)


def test_rejected_write_leaves_data_intact(source):
    with pytest.raises(ValueError):
        engine_utils.run_query(source, "WITH marker AS (SELECT 1) DELETE FROM items RETURNING id")
    assert engine_utils.run_query(source, "SELECT COUNT(*) FROM items")[1] == [[3]]


def test_query_deadline_interrupts_and_next_query_can_run(source):
    sql = """WITH RECURSIVE seq(x) AS (
        SELECT 1 UNION ALL SELECT x + 1 FROM seq WHERE x < 100000000
    ) SELECT SUM(x) FROM seq"""
    with pytest.raises(OperationalError, match="interrupted"):
        engine_utils.run_query_with_metadata(source, sql, timeout_seconds=0.002)
    assert engine_utils.run_query(source, "SELECT COUNT(*) FROM items")[1] == [[3]]


def test_external_sqlite_is_readonly_even_without_ast(tmp_path):
    path = tmp_path / "synthetic ?# 数据.sqlite"
    writer = create_engine(URL.create("sqlite", database=str(path)))
    with writer.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE items (id INTEGER)")
        conn.exec_driver_sql("INSERT INTO items VALUES (1)")
    writer.dispose()
    reader = engine_utils.build_engine({"type": "sqlite", "file_path": str(path)})
    try:
        assert engine_utils.run_query(reader, "SELECT id FROM items")[1] == [[1]]
        with reader.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA query_only").scalar() == 1
            conn.exec_driver_sql("PRAGMA query_only = OFF")
            with pytest.raises(OperationalError, match="readonly"):
                conn.exec_driver_sql("DELETE FROM items")
    finally:
        reader.dispose()


def test_missing_sqlite_never_created(tmp_path):
    path = tmp_path / "missing.sqlite"
    with pytest.raises(FileNotFoundError):
        engine_utils.build_engine({"type": "sqlite", "file_path": str(path)})
    assert not path.exists()


def test_excel_types_duplicate_names_and_readonly(tmp_path):
    path = tmp_path / "source.xlsx"
    workbook = Workbook()
    first = workbook.active
    first.title = "sales-data"
    first.append(["amount", "recorded", "same-name", "same name", "same_name_2", "AMOUNT"])
    for amount in (3, 20, 100):
        first.append([amount, datetime(2026, 1, amount if amount < 25 else 25), 1, 2, 3, amount + 0.5])
    workbook.create_sheet("sales data").append(["empty column"])
    workbook.save(path)
    workbook.close()
    engine = engine_utils.build_engine({"type": "excel", "file_path": str(path)})
    try:
        columns = inspect(engine).get_columns("sales_data")
        assert [column["name"] for column in columns] == [
            "amount", "recorded", "same_name", "same_name_2", "same_name_2_2", "AMOUNT_2",
        ]
        assert "INTEGER" in str(columns[0]["type"])
        assert "DATETIME" in str(columns[1]["type"])
        assert "FLOAT" in str(columns[5]["type"])
        assert inspect(engine).get_table_names() == ["sales_data", "sales_data_2"]
        assert engine_utils.run_query(engine, "SELECT amount FROM sales_data ORDER BY amount")[1] == [[3], [20], [100]]
        assert engine_utils.run_query(engine, "SELECT MAX(amount), SUM(amount) FROM sales_data")[1] == [[100, 123]]
        assert engine_utils.run_query(engine, "SELECT strftime('%Y', recorded) FROM sales_data LIMIT 1")[1] == [["2026"]]
        with engine.connect() as conn:
            with pytest.raises(OperationalError, match="readonly"):
                conn.exec_driver_sql("DELETE FROM sales_data")
    finally:
        engine.dispose()


def test_credentials_preserve_url_characters():
    url = engine_utils._build_url({"type": "mysql", "username": "user@domain", "password": "a@b:/?#",
                                   "host": "db.example", "database": "business"})
    assert url.username == "user@domain"
    assert url.password == "a@b:/?#"
    assert url.host == "db.example"


def test_executable_mysql_comment_not_sent_to_database():
    tree = engine_utils.validate_query("SELECT /*!50000 SLEEP(30) */ 1", "mysql")
    assert tree.sql(dialect="mysql", comments=False) == "SELECT 1"
