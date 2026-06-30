"""安全工具：密码哈希(bcrypt)、JWT、AES-256-CBC 加密、登录锁定、当前用户依赖。"""
import base64
import hashlib
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from . import models

bearer_scheme = HTTPBearer(auto_error=False)

ALGORITHM = "HS256"


# ---------- 密码哈希（直接用 bcrypt，避开已停更的 passlib 与新版 bcrypt 不兼容的问题）----------
def _pw_bytes(password: str) -> bytes:
    # bcrypt 仅使用前 72 字节，超长会报错；中文等多字节密码可能超过 72 字节，故截断
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_pw_bytes(plain), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------- JWT ----------
def create_token(user: "models.User", remember: bool = False) -> tuple:
    hours = settings.TOKEN_REMEMBER_HOURS if remember else settings.TOKEN_EXPIRE_HOURS
    expire = datetime.now(timezone.utc) + timedelta(hours=hours)
    payload = {
        "sub": user.id,
        "username": user.username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)
    return token, expire


# ---------- AES-256-CBC ----------
def _aes_key() -> bytes:
    # 由任意长度的 AES_KEY 派生出固定 32 字节密钥
    return hashlib.sha256(settings.AES_KEY.encode("utf-8")).digest()


def aes_encrypt(plaintext: str) -> str:
    if plaintext is None or plaintext == "":
        return ""
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(_aes_key()), modes.CBC(iv))
    enc = cipher.encryptor()
    ct = enc.update(data) + enc.finalize()
    return base64.b64encode(iv + ct).decode("utf-8")


def aes_decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        raw = base64.b64decode(token)
        iv, ct = raw[:16], raw[16:]
        cipher = Cipher(algorithms.AES(_aes_key()), modes.CBC(iv))
        dec = cipher.decryptor()
        padded = dec.update(ct) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
    except Exception:
        return ""


# ---------- 登录失败锁定（内存实现：5 次错误锁定 15 分钟）----------
_login_failures: dict = {}  # username -> [失败次数, 锁定截止时间戳]
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60


def is_locked(username: str) -> bool:
    rec = _login_failures.get(username)
    return bool(rec and rec[1] > time.time())


def record_failure(username: str):
    rec = _login_failures.get(username, [0, 0])
    rec[0] += 1
    if rec[0] >= MAX_FAILURES:
        rec[1] = time.time() + LOCK_SECONDS
    _login_failures[username] = rec


def clear_failures(username: str):
    _login_failures.pop(username, None)


# ---------- 当前用户依赖 ----------
def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> "models.User":
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "unauthorized", "message": "未授权访问"},
        )
    try:
        payload = jwt.decode(credentials.credentials, settings.SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "message": "Token无效或已过期"},
        )
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_token", "message": "Token无效或已过期"},
        )
    return user
