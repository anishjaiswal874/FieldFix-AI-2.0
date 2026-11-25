"""Persisted account and application controls used by authentication and Admin."""
from dataclasses import dataclass

from sqlalchemy import text
from sqlmodel import Session


@dataclass(frozen=True)
class AccountPolicy:
    revision: int = 0
    archived: bool = False
    temporary_until: int | None = None


def account_policy(session: Session, user_id: int) -> AccountPolicy:
    row = session.execute(text(
        "SELECT revision, archived, temporary_until FROM account_policy WHERE user_id=:uid"
    ), {"uid": user_id}).first()
    return AccountPolicy(row.revision, bool(row.archived), row.temporary_until) if row else AccountPolicy()


def registration_enabled(session: Session) -> bool:
    value = session.execute(text("SELECT requester_self_registration FROM app_settings WHERE id=1")).scalar()
    return value != 0
