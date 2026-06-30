"""用户：个人信息 / 更新信息 / 修改密码。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..database import get_db
from ..utils import api_error, log_action

router = APIRouter(prefix="/api/user", tags=["user"])


@router.get("/profile")
def get_profile(db: Session = Depends(get_db), user=Depends(security.get_current_user)):
    return {"user": {
        "id": user.id, "username": user.username, "phone": user.phone,
        "created_at": user.created_at.isoformat(),
    }}


@router.put("/profile")
def update_profile(body: schemas.UpdateProfileRequest, db: Session = Depends(get_db),
                   user=Depends(security.get_current_user)):
    if body.username and body.username != user.username:
        exists = db.query(models.User).filter(models.User.username == body.username).first()
        if exists:
            raise api_error(400, "username_exists", "用户名已存在")
        user.username = body.username
    db.commit()
    return {"message": "个人信息更新成功"}


@router.post("/change-password")
def change_password(body: schemas.ChangePasswordRequest, db: Session = Depends(get_db),
                    user=Depends(security.get_current_user)):
    if not security.verify_password(body.old_password, user.password_hash):
        raise api_error(400, "invalid_password", "旧密码错误")
    if body.old_password == body.new_password:
        raise api_error(400, "bad_request", "新密码不能与旧密码相同")
    user.password_hash = security.hash_password(body.new_password)
    db.commit()
    log_action(db, "change_password", "user", "修改密码", user_id=user.id)
    return {"message": "密码修改成功"}
