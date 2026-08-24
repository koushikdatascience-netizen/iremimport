"""Minimal in-process session state for Phase 1."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4


@dataclass
class AutomationSession:
    session_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: Optional[datetime] = None
    status: str = "active"
    last_error: Optional[str] = None


class SessionManager:
    _active_session: Optional[AutomationSession] = None

    @classmethod
    def create_session(cls) -> str:
        cls._active_session = AutomationSession(session_id=str(uuid4()))
        return cls._active_session.session_id

    @classmethod
    def end_session(cls) -> None:
        if cls._active_session:
            cls._active_session.ended_at = datetime.now(timezone.utc)
            cls._active_session.status = "ended"
            cls._active_session = None

    @classmethod
    def get_session_id(cls) -> Optional[str]:
        return cls._active_session.session_id if cls._active_session else None

    @classmethod
    def get_last_error(cls) -> Optional[str]:
        return cls._active_session.last_error if cls._active_session else None

    @classmethod
    def set_last_error(cls, error: str) -> None:
        if cls._active_session:
            cls._active_session.last_error = error
