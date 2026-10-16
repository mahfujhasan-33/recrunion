from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.auth import RecruiterSession, User


class AuthRepository:
    """Own account and revocable recruiter-session persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_user_by_username(self, username: str) -> User | None:
        return self._session.scalar(select(User).where(User.username == username))

    def get_user(self, user_id: UUID) -> User | None:
        return self._session.get(User, user_id)

    def has_users(self) -> bool:
        return self._session.scalar(select(User.id).limit(1)) is not None

    def save_user(self, user: User) -> User:
        self._session.add(user)
        self._session.commit()
        self._session.refresh(user)
        return user

    def create_session(self, session: RecruiterSession, now: datetime) -> None:
        self._session.execute(delete(RecruiterSession).where(RecruiterSession.expires_at <= now))
        self._session.add(session)
        self._session.commit()

    def get_session(self, session_id: UUID) -> RecruiterSession | None:
        return self._session.get(RecruiterSession, session_id)

    def revoke_session(self, session_id: UUID) -> None:
        self._session.execute(delete(RecruiterSession).where(RecruiterSession.id == session_id))
        self._session.commit()
