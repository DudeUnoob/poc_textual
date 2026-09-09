from datetime import datetime, timedelta

import pytest

from workbench.db import Base, SessionLocal, engine
from workbench.leases import (
    LeaseError,
    acquire_lease,
    release_lease,
    renew_lease,
    require_lease,
)


def test_row_lease_conflict_renew_and_revision(monkeypatch):
    Base.metadata.create_all(engine)
    now = datetime(2026, 1, 1)
    monkeypatch.setattr("workbench.leases._now", lambda: now)
    with SessionLocal() as session:
        lease = acquire_lease(session, "run:1:page:1:line:1", "u1", "tab-a")
        with pytest.raises(LeaseError, match="Another browser"):
            acquire_lease(session, "run:1:page:1:line:1", "u2", "tab-b")
        renewed = renew_lease(
            session, "run:1:page:1:line:1", "u1", "tab-a", lease["token"],
        )
        held = require_lease(
            session, "run:1:page:1:line:1", "u1", "tab-a",
            lease["token"], renewed["revision"],
        )
        release_lease(session, held)
        with pytest.raises(LeaseError, match="expired"):
            require_lease(
                session, "run:1:page:1:line:1", "u1", "tab-a",
                lease["token"], renewed["revision"],
            )


def test_expired_row_lease_can_be_reclaimed(monkeypatch):
    Base.metadata.create_all(engine)
    clock = {"now": datetime(2026, 1, 1)}
    monkeypatch.setattr("workbench.leases._now", lambda: clock["now"])
    with SessionLocal() as session:
        first = acquire_lease(session, "run:2:page:1:line:1", "u1", "tab-a")
        clock["now"] += timedelta(seconds=121)
        second = acquire_lease(session, "run:2:page:1:line:1", "u2", "tab-b")
        assert second["token"] != first["token"]
