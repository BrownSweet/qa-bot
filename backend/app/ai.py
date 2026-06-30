"""DeepSeek API 封装：NL2SQL 生成、结果分析（流式）、连接测试。"""
import json
import re
from typing import AsyncGenerator

import httpx
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .security import aes_decrypt

SQL_PROMPT = """你是一个SQL专家。请根据以下数据库Schema和用户问题，生成正确的SQL语句。

数据库Schema:
{schema}

用户问题:
{question}

请只输出一条可执行的SQL查询语句（SELECT），不要包含任何解释、注释或Markdown代码块标记。"""

ANALYZE_PROMPT = """请根据以下SQL查询结果，用自然语言给出分析报告。

查询问题:
{question}

执行的SQL:
{sql}

查询结果(JSON):
{result}

请用友好、专业的语言进行分析，使用Markdown格式（可包含表格、加粗、列表）。"""


def get_ai_config(db: Session) -> dict:
    """优先使用系统配置表，回退到环境变量。返回明文 api_key。"""
    cfg = db.query(models.SystemConfig).first()
    api_key = ""
    api_url = settings.DEEPSEEK_API_URL
    timeout = 30
    if cfg:
        api_key = aes_decrypt(cfg.api_key) if cfg.api_key else ""
        api_url = cfg.api_url or api_url
        timeout = cfg.timeout or 30
    if not api_key:
        api_key = settings.DEEPSEEK_API_KEY
    return {"api_key": api_key, "api_url": api_url.rstrip("/"), "timeout": timeout}


def _strip_sql(text_in: str) -> str:
    """去掉 ```sql ... ``` 包裹，取出纯 SQL。"""
    m = re.search(r"```(?:sql)?\s*(.+?)```", text_in, re.S | re.I)
    sql = m.group(1) if m else text_in
    return sql.strip().rstrip(";").strip()


async def generate_sql(cfg: dict, schema: str, question: str) -> str:
    prompt = SQL_PROMPT.format(schema=schema, question=question)
    async with httpx.AsyncClient(timeout=cfg["timeout"]) as client:
        resp = await client.post(
            f"{cfg['api_url']}/v1/chat/completions",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            json={
                "model": settings.DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "temperature": 0,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return _strip_sql(data["choices"][0]["message"]["content"])


async def analyze_stream(cfg: dict, question: str, sql: str, columns, rows) -> AsyncGenerator[str, None]:
    """流式生成分析报告，逐块 yield 文本。"""
    result_json = json.dumps(
        {"columns": columns, "rows": rows[:50]}, ensure_ascii=False, default=str
    )
    prompt = ANALYZE_PROMPT.format(question=question, sql=sql, result=result_json)
    async with httpx.AsyncClient(timeout=cfg["timeout"]) as client:
        async with client.stream(
            "POST",
            f"{cfg['api_url']}/v1/chat/completions",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            json={
                "model": settings.DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "temperature": 0.3,
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    delta = chunk["choices"][0]["delta"].get("content")
                    if delta:
                        yield delta
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


async def test_connection(cfg: dict) -> tuple:
    """测试 AI 服务连接，返回 (success, message)。"""
    if not cfg["api_key"]:
        return False, "未配置 API Key"
    try:
        async with httpx.AsyncClient(timeout=cfg["timeout"]) as client:
            resp = await client.post(
                f"{cfg['api_url']}/v1/chat/completions",
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
                json={
                    "model": settings.DEEPSEEK_MODEL,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
        if resp.status_code == 200:
            return True, "AI服务连接成功"
        if resp.status_code in (401, 403):
            return False, "API Key无效"
        return False, f"AI服务返回状态码 {resp.status_code}"
    except Exception as e:
        return False, f"连接失败：{e}"
