import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

PBKDF2_ITERATIONS = 120_000
TOKEN_TTL = timedelta(days=7)


class AuthRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=64)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=6, max_length=64)
    new_password: str = Field(min_length=6, max_length=64)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    if stored.startswith("pbkdf2$"):
        _, salt, digest = stored.split("$", 2)
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            PBKDF2_ITERATIONS,
        ).hex()
        return hmac.compare_digest(candidate, digest)

    # 旧库中的无盐 SHA-256 哈希，仅用于兼容登录，登录成功后立即升级
    legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy, stored)


def issue_token(user: User) -> None:
    user.token = secrets.token_urlsafe(32)
    user.token_expires_at = datetime.utcnow() + TOKEN_TTL


def user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
    }


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录")

    token = authorization.removeprefix("Bearer ").strip()
    user = db.query(User).filter(User.token == token).first()

    if not user:
        raise HTTPException(status_code=401, detail="登录已失效")

    if user.token_expires_at and user.token_expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")

    return user


@router.post("/register")
def register(req: AuthRequest, db: Session = Depends(get_db)):
    username = req.username.strip()
    exists = db.query(User).filter(User.username == username).first()

    if exists:
        raise HTTPException(status_code=400, detail="用户名已存在")

    user = User(
        username=username,
        password_hash=hash_password(req.password),
    )
    issue_token(user)

    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "token": user.token,
        "user": user_payload(user),
    }


@router.post("/login")
def login(req: AuthRequest, db: Session = Depends(get_db)):
    username = req.username.strip()
    user = db.query(User).filter(User.username == username).first()

    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=400, detail="账号或密码错误")

    if not user.password_hash.startswith("pbkdf2$"):
        user.password_hash = hash_password(req.password)

    issue_token(user)
    db.commit()
    db.refresh(user)

    return {
        "token": user.token,
        "user": user_payload(user),
    }


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {
        "user": user_payload(current_user),
    }


@router.post("/logout")
def logout(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    current_user.token = None
    current_user.token_expires_at = None
    db.commit()
    return {"message": "已退出登录"}


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(req.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")

    if req.old_password == req.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")

    current_user.password_hash = hash_password(req.new_password)
    issue_token(current_user)
    db.commit()
    db.refresh(current_user)

    return {
        "message": "密码修改成功",
        "token": current_user.token,
        "user": user_payload(current_user),
    }
