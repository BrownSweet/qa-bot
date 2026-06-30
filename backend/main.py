"""问答机器人后端入口（FastAPI）。"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import init_db
from app.routers import (
    auth, chat, db_config, excel, export, logs, notifications, sessions, system, user,
)

app = FastAPI(title="问答机器人 API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """统一错误格式为 {error, message}。"""
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": _code_for(exc.status_code), "message": str(detail)},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    msg = first.get("msg", "请求参数错误")
    return JSONResponse(
        status_code=400,
        content={"error": "bad_request", "message": f"{field}: {msg}" if field else msg},
    )


def _code_for(status_code: int) -> str:
    return {
        400: "bad_request", 401: "unauthorized", 403: "forbidden",
        404: "not_found", 429: "rate_limit", 500: "server_error",
    }.get(status_code, "error")


for r in (auth, db_config, sessions, chat, system, user, excel, logs, export, notifications):
    app.include_router(r.router)


@app.get("/")
def root():
    return {"name": "问答机器人 API", "version": "1.0", "docs": "/docs"}
