from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import User


class UserRepository:
    def __init__(self, session: Session):
        self.session = session

    def ensure_local(self, username: str = "local-user", timezone_name: str = "Asia/Shanghai") -> User:
        user = self.session.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(username=username, timezone=timezone_name)
            self.session.add(user)
            self.session.flush()
        return user
