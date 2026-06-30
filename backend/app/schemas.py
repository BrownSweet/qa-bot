"""Pydantic 请求/响应模型（pydantic v2）。"""
from typing import List, Optional

from pydantic import BaseModel, Field


# ===== 认证 =====
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=20)
    password: str = Field(..., min_length=6, max_length=32)
    remember_me: Optional[bool] = False


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=20)
    password: str = Field(..., min_length=6, max_length=32)
    phone: str = Field(..., min_length=11, max_length=11)
    code: str = Field(..., min_length=6, max_length=6)


class SendCodeRequest(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11)
    type: str = Field(..., pattern="^(register|forgot)$")


class ForgotPasswordRequest(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11)
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=6, max_length=32)


# ===== 数据库配置 =====
class DBConfigRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    type: str = Field(..., pattern="^(mysql|postgresql|sqlite|excel)$")
    host: Optional[str] = None
    port: Optional[int] = Field(None, ge=1, le=65535)
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    file_path: Optional[str] = None


class TestConnectionRequest(BaseModel):
    type: str = Field(..., pattern="^(mysql|postgresql|sqlite|excel)$")
    host: Optional[str] = None
    port: Optional[int] = Field(None, ge=1, le=65535)
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    file_path: Optional[str] = None


# ===== 会话 =====
class CreateSessionRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=100)


class UpdateSessionRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    is_pinned: Optional[bool] = None


# ===== 问答 =====
class SendMessageRequest(BaseModel):
    session_id: str
    db_config_id: str
    question: str = Field(..., min_length=1, max_length=1000)


# ===== 系统配置 =====
class UpdateConfigRequest(BaseModel):
    api_key: Optional[str] = None
    api_url: Optional[str] = None
    timeout: Optional[int] = Field(None, ge=1, le=60)


# ===== 用户 =====
class UpdateProfileRequest(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=20)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=6, max_length=32)
    new_password: str = Field(..., min_length=6, max_length=32)


# ===== Excel =====
class ParseExcelRequest(BaseModel):
    file_path: str


class GetSheetDataRequest(BaseModel):
    file_path: str
    sheet_name: str
    limit: Optional[int] = 100


# ===== 导出 =====
class ExportRequest(BaseModel):
    session_id: str
    format: str = Field(..., pattern="^(excel|csv)$")


# ===== 通知 =====
class UpdateNotificationRequest(BaseModel):
    is_read: Optional[bool] = None
