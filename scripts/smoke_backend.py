"""Exercise the actual frozen service in an isolated data directory; no AI/network account required."""
import csv
import io
import json
import os
from pathlib import Path
import queue
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import zipfile
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BUILD = Path(os.getenv("QA_BUILD_ROOT", str(ROOT / "build"))).resolve()


class SyntheticAI(BaseHTTPRequestHandler):
    """Local fixture exercises real HTTP and SSE without a provider account."""
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append(body)
        self.send_response(200)
        if body.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            message = {"choices": [{"delta": {"content": self.server.answer}}]}
            terminal = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(("data: " + json.dumps(message) + "\n\ndata: " + json.dumps(terminal) + "\n\ndata: [DONE]\n\n").encode())
        else:
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"choices": [{"message": {"content": "SELECT SUM(amount) AS total FROM sales"}}]}).encode())

    def log_message(self, *_args):
        pass


def main():
    binary = BUILD / "backend/qa-backend" / ("qa-backend.exe" if sys.platform == "win32" else "qa-backend")
    command = [str(binary)]
    if sys.platform == "win32":
        embedded = BUILD / "backend-windows/qa-backend"
        command = [str(embedded / "python.exe"), str(embedded / "desktop_server.py")]
    if "--source" in sys.argv:
        command = [sys.executable, str(ROOT / "backend/desktop_server.py")]
    with tempfile.TemporaryDirectory(prefix="qa-robot-smoke-") as temp:
        provider = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticAI)
        provider.calls = []
        provider.answer = "测试数据合计为 10。"
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        token = secrets.token_hex(32)
        env = dict(os.environ, QA_DESKTOP_MODE="1", QA_DATA_DIR=str(Path(temp)/"data"),
                   QA_WEB_DIR=str(ROOT/"frontend/dist"), QA_DESKTOP_TOKEN=token, PYTHON_DOTENV_DISABLED="1")
        ready = queue.Queue()
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=env, cwd=temp)
        def read_stdout():
            for line in process.stdout:
                if line.startswith("QA_DESKTOP_READY "):
                    ready.put(json.loads(line.split(" ", 1)[1]))
        threading.Thread(target=read_stdout, daemon=True).start()
        try:
            try:
                port = ready.get(timeout=60)["port"]
            except queue.Empty:
                process.kill()
                raise RuntimeError("后台启动超时: " + process.stderr.read())
            base = f"http://127.0.0.1:{port}"
            def request(path, method="GET", data=None, authenticated=True, jwt=None):
                headers = {"Content-Type":"application/json"}
                if authenticated: headers["X-Desktop-Token"] = token
                if jwt: headers["Authorization"] = "Bearer " + jwt
                req = Request(base+path, method=method, headers=headers,
                              data=None if data is None else json.dumps(data).encode())
                try:
                    with urlopen(req, timeout=10) as response:
                        content = response.read()
                        return response.status, json.loads(content) if "json" in response.headers.get("Content-Type", "") else content
                except HTTPError as error:
                    return error.code, json.loads(error.read())
            assert request("/health", authenticated=False)[0] == 401
            assert request("/api/desktop/session", authenticated=False)[0] == 401
            assert request("/health")[1]["status"] == "ready"
            status, auth = request("/api/desktop/session")
            assert status == 200 and auth["user"]["id"] == "desktop-owner"
            jwt = auth["token"]
            assert request("/api/user/profile", jwt=jwt)[0] == 200
            assert request("/api/auth/send-code", "POST", {"phone":"13800000000","type":"forgot"})[0] == 404
            _, session = request("/api/sessions", "POST", {"name":"桌面验证"}, jwt=jwt)
            assert session["session"]["name"] == "桌面验证"
            assert request("/api/sessions", jwt=jwt)[1]["sessions"][0]["name"] == "桌面验证"
            assert request("/api/db/configs", "POST", {"name":"forbidden", "type":"sqlite", "file_path":str(Path(temp)/"data/qabot.db")}, jwt=jwt)[0] == 403
            fixture = Path(temp)/"sample.sqlite"
            with closing(sqlite3.connect(fixture)) as connection, connection:
                connection.execute("CREATE TABLE sales(amount REAL)")
                connection.execute("INSERT INTO sales VALUES (10)")
            status, result = request("/api/db/test-connection", "POST", {"type":"sqlite", "file_path":str(fixture)}, jwt=jwt)
            assert status == 200 and result["success"], result
            _, source = request("/api/db/configs", "POST", {"name":"sample", "type":"sqlite", "file_path":str(fixture)}, jwt=jwt)
            source_id = source["config"]["id"]
            session_id = session["session"]["id"]
            semantics = "业务口径：sales.amount 是已确认销售金额，汇总时只按当前数据库数据计算。"
            status, saved = request(f"/api/db/configs/{source_id}/semantics", "PUT", {"context":semantics}, jwt=jwt)
            assert status == 200 and saved["context"] == semantics and not saved["stale"], saved
            assert request(f"/api/db/configs/{source_id}/semantics", jwt=jwt)[1]["context"] == semantics
            assert request("/api/system/config", "PUT", {"api_url":f"http://127.0.0.1:{provider.server_port}", "api_key":"synthetic-key-not-a-real-secret"}, jwt=jwt)[0] == 200
            status, answer = request("/api/chat/send", "POST", {"session_id":session_id, "db_config_id":source_id, "question":"销售额合计是多少？"}, jwt=jwt)
            assert status == 200 and '测试数据合计为 10。' in answer.decode(), answer
            assert '"status": "completed"' in answer.decode(), answer
            history = request("/api/chat/messages/"+session_id, jwt=jwt)[1]["messages"]
            assert history[-1]["status"] == "completed" and '10' in history[-1]["content"], history
            assert history[-1]["evidence"]["rows"] == [[10]], history[-1]
            first_snapshot = history[-1]["generation_snapshot"]
            assert first_snapshot["business_context"] == semantics and first_snapshot["question"] == "销售额合计是多少？", first_snapshot
            assert first_snapshot["model"] == "deepseek-chat" and "sales" in first_snapshot["schema"], first_snapshot
            assert first_snapshot["history"] == [] and first_snapshot["task"] is None, first_snapshot
            assert first_snapshot["api_url"] == f"http://127.0.0.1:{provider.server_port}", first_snapshot
            assert "synthetic-key-not-a-real-secret" not in json.dumps(first_snapshot), first_snapshot
            sql_prompts = [call["messages"][-1]["content"] for call in provider.calls if not call.get("stream")]
            assert any(semantics in prompt for prompt in sql_prompts), sql_prompts

            status, created = request("/api/analysis-tasks", "POST", {
                "name":"销售额", "question_template":"统计{{region}}销售额", "db_config_id":source_id,
            }, jwt=jwt)
            assert status == 201 and created["task"]["parameters"] == ["region"], created
            task_id = created["task"]["id"]

            def run_task(region):
                parameters = {"region": region}
                status, prepared = request(f"/api/analysis-tasks/{task_id}/prepare", "POST",
                                           {"parameters":parameters}, jwt=jwt)
                assert status == 200 and prepared["question"] == f"统计{region}销售额", prepared
                status, streamed = request("/api/chat/send", "POST", {
                    "session_id":session_id, **prepared, "analysis_task_parameters":parameters,
                }, jwt=jwt)
                assert status == 200 and b"event: complete" in streamed and b'"status": "completed"' in streamed, streamed

            run_task("东区")
            with closing(sqlite3.connect(fixture)) as connection, connection:
                connection.execute("UPDATE sales SET amount = 20")
            provider.answer = "测试数据合计为 20。"
            run_task("西区")
            status, listed = request(f"/api/analysis-tasks/{task_id}/runs", jwt=jwt)
            assert status == 200 and listed["total"] == 2, listed
            runs = {run["question"]: run for run in listed["runs"]}
            east, west = runs["统计东区销售额"], runs["统计西区销售额"]
            assert east["status"] == west["status"] == "completed", runs
            assert east["source"] == west["source"] and east["source"]["id"] == source_id, runs
            assert east["evidence"]["sql"] == west["evidence"]["sql"], runs
            assert east["evidence"]["rows"] == [[10]] and west["evidence"]["rows"] == [[20]], runs
            assert east["generation_snapshot"]["task"] == {
                "id":task_id, "name":"销售额", "question_template":"统计{{region}}销售额",
                "parameters":{"region":"东区"},
            }, east
            assert west["generation_snapshot"]["task"]["parameters"] == {"region":"西区"}, west
            status, changed_task = request(f"/api/analysis-tasks/{task_id}", "PUT", {
                "name":"新销售任务", "question_template":"比较{{period}}销售额", "db_config_id":source_id,
            }, jwt=jwt)
            assert status == 200 and changed_task["task"]["name"] == "新销售任务", changed_task
            old_run = next(run for run in request(f"/api/analysis-tasks/{task_id}/runs", jwt=jwt)[1]["runs"]
                           if run["question"] == "统计东区销售额")
            assert old_run["generation_snapshot"]["task"]["name"] == "销售额", old_run

            original_sql = "SELECT SUM(amount) AS total FROM sales"
            status, created = request("/api/evaluation/cases", "POST", {
                "db_config_id":source_id, "question":"销售总额是多少？", "expected_sql":original_sql,
            }, jwt=jwt)
            assert status == 201 and created["case"]["baseline"]["rows"] == [[20]], created
            case_id = created["case"]["id"]
            status, evaluated = request(f"/api/evaluation/cases/{case_id}/run", "POST", jwt=jwt)
            assert status == 200, evaluated
            first_run = evaluated["run"]
            assert first_run["status"] == "passed" and first_run["matched"] and not first_run["baseline_changed"], first_run
            assert first_run["case_snapshot"]["expected_sql"] == original_sql, first_run
            assert first_run["generation_snapshot"]["business_context"] == semantics, first_run
            assert first_run["generation_snapshot"]["task"] is None, first_run
            updated_sql = "SELECT SUM(amount) AS total FROM sales WHERE amount > 0"
            status, updated = request(f"/api/evaluation/cases/{case_id}", "PUT", {
                "db_config_id":source_id, "question":"正销售额是多少？", "expected_sql":updated_sql,
            }, jwt=jwt)
            assert status == 200 and updated["case"]["expected_sql"] == updated_sql, updated
            updated_semantics = semantics + " 此后口径由管理员再次确认。"
            assert request(f"/api/db/configs/{source_id}/semantics", "PUT", {
                "context":updated_semantics,
            }, jwt=jwt)[0] == 200
            with closing(sqlite3.connect(fixture)) as connection, connection:
                connection.execute("UPDATE sales SET amount = 30")
            status, evaluated = request(f"/api/evaluation/cases/{case_id}/run", "POST", jwt=jwt)
            assert status == 200, evaluated
            second_run = evaluated["run"]
            assert second_run["status"] == "passed" and second_run["matched"] and second_run["baseline_changed"], second_run
            assert second_run["case_snapshot"]["expected_sql"] == updated_sql, second_run
            assert second_run["generation_snapshot"]["business_context"] == updated_semantics, second_run
            saved_runs = request(f"/api/evaluation/cases/{case_id}/runs", jwt=jwt)[1]["runs"]
            preserved = next(run for run in saved_runs if run["id"] == first_run["id"])
            assert preserved["case_snapshot"]["expected_sql"] == original_sql, saved_runs
            assert preserved["case_snapshot"]["baseline"]["rows"] == [[20]], saved_runs
            assert preserved["generation_snapshot"]["business_context"] == semantics, preserved

            status, result_csv = request("/api/export-result", "POST", {
                "message_id":west["message_id"], "format":"csv",
            }, jwt=jwt)
            assert status == 200, result_csv
            result_rows = list(csv.reader(io.StringIO(result_csv.decode("utf-8-sig"))))
            assert result_rows[0][0] == "total" and result_rows[1][-2] == "metadata", result_rows
            scope = json.loads(result_rows[1][-1])
            assert scope["source"]["id"] == source_id and scope["sql"] == west["evidence"]["sql"], scope
            assert result_rows[2][-2] == "data" and float(result_rows[2][0]) == 20, result_rows
            status, session_csv = request("/api/export", "POST", {
                "session_id":session_id, "format":"csv",
            }, jwt=jwt)
            assert status == 200, session_csv
            session_rows = list(csv.reader(io.StringIO(session_csv.decode("utf-8-sig"))))
            header = session_rows[0]
            record = next(row for row in session_rows[1:] if row[header.index("消息ID")] == west["message_id"])
            assert record[header.index("执行SQL")] == west["evidence"]["sql"], record
            assert json.loads(record[header.index("来源快照(JSON)")])["id"] == source_id, record
            assert json.loads(record[header.index("已保存结果预览(JSON或证据页)")]) == [[20]], record
            exported_snapshot = json.loads(record[header.index("生成输入快照(JSON或证据页)")])
            assert exported_snapshot["task"]["parameters"] == {"region":"西区"}, exported_snapshot
            for path, body in (("/api/export-result", {"message_id":west["message_id"], "format":"excel"}),
                               ("/api/export", {"session_id":session_id, "format":"excel"})):
                status, workbook = request(path, "POST", body, jwt=jwt)
                assert status == 200 and zipfile.is_zipfile(io.BytesIO(workbook)), (path, status)
            assert request("/settings")[0] == 200
            assert request("/api/unknown")[0] == 404
            assert request("/api/sessions/"+session_id, "DELETE", jwt=jwt)[0] == 200
            process.stdin.close()
            assert process.wait(timeout=12) == 0
            print(json.dumps({"backendSmoke":"passed", "mode":"source" if "--source" in sys.argv else "packaged",
                              "checks":["loopback-auth", "auto-session", "disabled-public-auth", "session-crud", "internal-db-denied", "sqlite-source", "source-semantics", "mock-ai-query-sse-history", "generation-input-snapshots", "parameterized-task-runs-comparison", "golden-evaluation-snapshot-drift", "result-and-session-csv-excel-export", "spa", "graceful-exit"]}))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            provider.shutdown()
            provider.server_close()


if __name__ == "__main__":
    main()
