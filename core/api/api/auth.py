"""
B 端会话签发（YYC3 双项目衔接 · M4 最小可用面）
- POST /v1/auth/login   账号口令 → JWT 签发（免鉴权；账号源 env YYC3_B_USERS_JSON，仅存 SHA-256 哈希，密钥零入库）
- GET  /v1/auth/verify  Bearer JWT 有效性回执（无效令牌在 AuthMiddleware 即 401；本端点独立再校验保证语义自足）

账号源格式（env YYC3_B_USERS_JSON）：
  [{"username":"...","password_sha256":"<sha256 hex>","user_id":"...","role":"Director"}]
空表 → login 503（服务未配置 B 端账号）；用户库/注册审核流随 M5 演进。
"""
import hashlib
import hmac
import json
import logging
import os
from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from app.middleware.auth import auth_config, generate_jwt_token, verify_jwt_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["🔐 会话鉴权"])


class LoginIn(BaseModel):
    username: str
    password: str


class LoginOut(BaseModel):
    token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    role: str
    expires_hours: int


class VerifyOut(BaseModel):
    valid: bool
    user_id: Optional[str] = None


def _load_b_users() -> List[dict]:
    """从 env 解析 B 端账号表；解析失败按空表处理（不泄露配置细节）"""
    raw = os.getenv("YYC3_B_USERS_JSON", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("YYC3_B_USERS_JSON 解析失败，按空账号表处理")
        return []
    return data if isinstance(data, list) else []


@router.post("/login", response_model=LoginOut)
def login(payload: LoginIn) -> LoginOut:
    users = _load_b_users()
    if not users:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="B 端账号源未配置（YYC3_B_USERS_JSON）",
        )
    digest = hashlib.sha256(payload.password.encode("utf-8")).hexdigest()
    for u in users:
        if u.get("username") == payload.username and hmac.compare_digest(
            digest, str(u.get("password_sha256", ""))
        ):
            token = generate_jwt_token(str(u["user_id"]))
            return LoginOut(
                token=token,
                user_id=str(u["user_id"]),
                username=payload.username,
                role=str(u.get("role", "Director")),
                expires_hours=auth_config.JWT_EXPIRATION_HOURS,
            )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或口令错误")


@router.get("/verify", response_model=VerifyOut)
def verify(authorization: Optional[str] = Header(default=None)) -> VerifyOut:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少 Bearer 令牌")
    payload = verify_jwt_token(authorization.split(" ", 1)[1].strip())
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期")
    return VerifyOut(valid=True, user_id=payload.get("user_id"))
