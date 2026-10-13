from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from backend.app.api.errors import BusinessError
from backend.app.models import User
from backend.app.security import decode_access_token


def get_session(request: Request) -> Generator[Session, None, None]:
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


def current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise BusinessError("AUTH_REQUIRED", "请先登录。", 401)
    try:
        user_id = decode_access_token(authorization[7:], request.app.state.settings.secret_key)
    except ValueError as exc:
        raise BusinessError("INVALID_TOKEN", "登录状态已失效，请重新登录。", 401) from exc
    user = session.get(User, user_id)
    if user is None:
        raise BusinessError("INVALID_TOKEN", "用户不存在。", 401)
    return user
