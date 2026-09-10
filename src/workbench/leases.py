"""Local edit leases so two browsers cannot silently overwrite the same row.

Supabase deployments use CloudRepository leases for Supabase Postgres rows. While the
Jinja review screens still persist to SQLite, this table is the equivalent
guard: acquire on GET, require on POST, expire after two minutes.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .db import Base, utc_now

LEASE_SECONDS = 120


class ReviewLease(Base):
    __tablename__ = "review_leases"
    row_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    uid: Mapped[str] = mapped_column(String(128))
    session_id: Mapped[str] = mapped_column(String(64))
    token: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class LeaseError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def review_session_id(request) -> str:
    existing = (getattr(request, "cookies", None) or {}).get("review_session")
    if existing:
        return str(existing)
    issued = getattr(request.state, "issued_review_session", None)
    if issued:
        return str(issued)
    token = secrets.token_urlsafe(16)
    request.state.issued_review_session = token
    return token


def _now() -> datetime:
    return utc_now()


def acquire_lease(session: Session, key: str, uid: str, session_id: str) -> dict:
    now = _now()
    lease = session.get(ReviewLease, key)
    if lease and lease.expires_at > now and (lease.uid != uid or lease.session_id != session_id):
        raise LeaseError("Another browser is editing this row")
    token = secrets.token_urlsafe(24)
    expires = now + timedelta(seconds=LEASE_SECONDS)
    if lease is None:
        lease = ReviewLease(
            row_key=key, uid=uid, session_id=session_id, token=token,
            revision=0, expires_at=expires,
        )
        session.add(lease)
    else:
        lease.uid = uid
        lease.session_id = session_id
        lease.token = token
        lease.expires_at = expires
    session.flush()
    return {
        "row_key": key,
        "token": lease.token,
        "session_id": session_id,
        "revision": lease.revision,
        "expires_at": lease.expires_at.isoformat() + "Z",
    }


def renew_lease(session: Session, key: str, uid: str, session_id: str, token: str) -> dict:
    lease = session.get(ReviewLease, key)
    _require(lease, uid, session_id, token)
    lease.expires_at = _now() + timedelta(seconds=LEASE_SECONDS)
    session.flush()
    return {
        "row_key": key,
        "token": lease.token,
        "session_id": session_id,
        "revision": lease.revision,
        "expires_at": lease.expires_at.isoformat() + "Z",
    }


def require_lease(session: Session, key: str, uid: str, session_id: str, token: str, expected_revision: int) -> ReviewLease:
    lease = session.get(ReviewLease, key)
    _require(lease, uid, session_id, token)
    if lease.revision != expected_revision:
        raise LeaseError("Row changed; reload before saving")
    return lease


def release_lease(session: Session, lease: ReviewLease) -> None:
    lease.revision += 1
    lease.expires_at = _now()
    session.flush()


def _require(lease: ReviewLease | None, uid: str, session_id: str, token: str) -> None:
    if (
        lease is None
        or lease.uid != uid
        or lease.session_id != session_id
        or lease.token != token
        or lease.expires_at <= _now()
    ):
        raise LeaseError("Edit lease expired or replaced")


def expired_keys(session: Session) -> list[str]:
    return list(session.scalars(select(ReviewLease.row_key).where(ReviewLease.expires_at <= _now())))
