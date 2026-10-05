"""问答机器人后端入口（FastAPI）。"""
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from sqlalchemy import text
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db, init_db
from app import models, security, analysis_tasks, semantic_context, quality_eval
from app.routers import (
    auth, chat, db_config, excel, export, logs, notifications, sessions, system, user,
)

app = FastAPI(title="问答机器人 API", version=settings.VERSION,
              docs_url=None if settings.DESKTOP_MODE else "/docs",
              redoc_url=None if settings.DESKTOP_MODE else "/redoc",
              openapi_url=None if settings.DESKTOP_MODE else "/openapi.json")

if not settings.DESKTOP_MODE:
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5555", "http://127.0.0.1:5555"],
                       allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def desktop_boundary(request: Request, call_next):
    if settings.DESKTOP_MODE:
        if request.url.hostname != "127.0.0.1":
            return JSONResponse(status_code=403, content={"error": "forbidden", "message": "仅支持本机访问"})
        path = request.url.path
        if path.startswith("/api/") or path == "/health":
            supplied = request.headers.get("X-Desktop-Token", "")
            if not secrets.compare_digest(supplied, settings.DESKTOP_TOKEN):
                return JSONResponse(status_code=401, content={"error": "unauthorized", "message": "请从桌面应用访问"})
        if path.startswith("/api/auth/") or path == "/api/user/change-password":
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "本地版无需注册或登录"})
    response = await call_next(request)
    if settings.DESKTOP_MODE:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


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
app.include_router(analysis_tasks.router)
app.include_router(semantic_context.router)
app.include_router(quality_eval.router)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    if settings.DESKTOP_MODE and not db.get(models.User, "desktop-owner"):
        return JSONResponse(status_code=503, content={"status": "starting"})
    return {"status": "ready", "version": settings.VERSION}


if settings.DESKTOP_MODE:
    @app.get("/api/desktop/session")
    def desktop_session(db: Session = Depends(get_db)):
        user = db.get(models.User, "desktop-owner")
        token, _ = security.create_token(user, remember=True)
        return {"token": token, "user": {"id": user.id, "username": user.username, "phone": ""}, "version": settings.VERSION}

    @app.get("/{asset_path:path}", include_in_schema=False)
    def desktop_ui(asset_path: str):
        web = Path(settings.WEB_DIR).resolve()
        candidate = (web / asset_path).resolve()
        if asset_path.startswith("api/") or not candidate.is_relative_to(web):
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "资源不存在"})
        if candidate.is_file():
            return FileResponse(candidate)
        if asset_path and Path(asset_path).suffix:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "资源不存在"})
        return FileResponse(web / "index.html")
else:
    @app.get("/")
    def root():
        return {"name": "问答机器人 API", "version": settings.VERSION, "docs": "/docs"}
