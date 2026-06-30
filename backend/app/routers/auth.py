"""认证：登录 / 注册 / 发送验证码 / 找回密码。"""
import random
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..database import gen_uuid, get_db
from ..utils import api_error, log_action

router = APIRouter(prefix="/api/auth", tags=["auth"])

CODE_COOLDOWN = timedelta(minutes=5)
CODE_TTL = timedelta(minutes=5)


@router.post("/login")
def login(body: schemas.LoginRequest, db: Session = Depends(get_db)):
    if security.is_locked(body.username):
        raise api_error(403, "forbidden", "账号已锁定，请15分钟后再试")

    user = db.query(models.User).filter(models.User.username == body.username).first()
    if not user or not security.verify_password(body.password, user.password_hash):
        security.record_failure(body.username)
        log_action(db, "login", "auth", f"登录失败: {body.username}", level="WARN")
        raise api_error(401, "invalid_credentials", "用户名或密码错误")

    security.clear_failures(body.username)
    token, expire = security.create_token(user, remember=bool(body.remember_me))
    log_action(db, "login", "auth", "用户登录成功", user_id=user.id)
    return {
        "token": token,
        "user": {"id": user.id, "username": user.username, "phone": user.phone},
        "expires_at": expire.isoformat(),
    }


@router.post("/register", status_code=201)
def register(body: schemas.RegisterRequest, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.username == body.username).first():
        raise api_error(400, "username_exists", "用户名已存在")
    if db.query(models.User).filter(models.User.phone == body.phone).first():
        raise api_error(400, "phone_exists", "手机号已被注册")

    if not _verify_code(db, body.phone, "register", body.code):
        raise api_error(400, "invalid_code", "验证码错误")

    user = models.User(
        id=gen_uuid(),
        username=body.username,
        phone=body.phone,
        password_hash=security.hash_password(body.password),
    )
    db.add(user)
    db.flush()  # 先落库 user，保证通知的外键约束通过（MySQL InnoDB 会在同一事务内校验 FK）
    db.add(models.Notification(
        id=gen_uuid(), user_id=user.id, title="欢迎使用问答机器人",
        content="您已成功注册，开始配置数据源并提问吧！", type="info",
    ))
    db.commit()
    log_action(db, "register", "auth", f"新用户注册: {body.username}", user_id=user.id)
    return {"message": "注册成功，请登录"}


@router.post("/send-code")
def send_code(body: schemas.SendCodeRequest, db: Session = Depends(get_db)):
    recent = (
        db.query(models.VerificationCode)
        .filter(models.VerificationCode.phone == body.phone,
                models.VerificationCode.type == body.type)
        .order_by(models.VerificationCode.sent_at.desc())
        .first()
    )
    if recent and datetime.utcnow() - recent.sent_at < CODE_COOLDOWN:
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limit", "message": "发送过于频繁，请5分钟后再试"},
        )

    code = f"{random.randint(0, 999999):06d}"
    db.add(models.VerificationCode(
        id=gen_uuid(), phone=body.phone, code=code, type=body.type,
        sent_at=datetime.utcnow(), used=False,
    ))
    db.commit()
    # 生产环境此处对接短信服务；开发环境直接把验证码返回，便于联调
    return {"message": "验证码已发送", "dev_code": code}


@router.post("/forgot-password")
def forgot_password(body: schemas.ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.phone == body.phone).first()
    if not user:
        raise api_error(404, "not_found", "该手机号未注册")
    if not _verify_code(db, body.phone, "forgot", body.code):
        raise api_error(400, "invalid_code", "验证码错误")

    user.password_hash = security.hash_password(body.new_password)
    db.commit()
    log_action(db, "forgot_password", "auth", "密码重置成功", user_id=user.id)
    return {"message": "密码重置成功，请登录"}


def _verify_code(db: Session, phone: str, type_: str, code: str) -> bool:
    record = (
        db.query(models.VerificationCode)
        .filter(models.VerificationCode.phone == phone,
                models.VerificationCode.type == type_,
                models.VerificationCode.used == False)  # noqa: E712
        .order_by(models.VerificationCode.sent_at.desc())
        .first()
    )
    if not record:
        return False
    if datetime.utcnow() - record.sent_at > CODE_TTL:
        return False
    if record.code != code:
        return False
    record.used = True
    db.commit()
    return True
