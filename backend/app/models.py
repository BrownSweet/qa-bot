"""SQLAlchemy 模型 —— 对应 SDD 中的全部数据表。"""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
)

from .database import Base, gen_uuid


class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    username = Column(String(20), nullable=False, unique=True)
    phone = Column(String(11), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class VerificationCode(Base):
    __tablename__ = "verification_codes"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    phone = Column(String(11), nullable=False, index=True)
    code = Column(String(6), nullable=False)
    type = Column(String(20), nullable=False)  # register / forgot
    sent_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    used = Column(Boolean, nullable=False, default=False)


class DBConfig(Base):
    __tablename__ = "db_configs"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(50), nullable=False)
    type = Column(String(20), nullable=False)  # mysql/postgresql/sqlite/excel
    host = Column(String(255))
    port = Column(Integer)
    database_name = Column(String(100))
    username = Column(String(100))
    password = Column(String(255))  # AES 加密后的密码
    file_path = Column(String(500))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatSession(Base):
    __tablename__ = "sessions"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    is_pinned = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    session_id = Column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)  # user / assistant
    content = Column(Text, nullable=False)
    status = Column(String(20), nullable=False)  # pending / completed / error
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SystemConfig(Base):
    __tablename__ = "system_configs"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    api_key = Column(String(255), nullable=False, default="")  # AES 加密
    api_url = Column(String(255), nullable=False, default="https://api.deepseek.com")
    timeout = Column(Integer, nullable=False, default=30)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Log(Base):
    __tablename__ = "logs"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), index=True)
    action = Column(String(50), nullable=False)
    module = Column(String(50), nullable=False)
    level = Column(String(20), nullable=False)  # INFO/WARN/ERROR
    message = Column(String(500), nullable=False)
    details = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(100), nullable=False)
    content = Column(Text, nullable=False)
    type = Column(String(20), nullable=False)  # info/warning/error
    is_read = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
