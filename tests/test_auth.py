from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from workbench.auth import (
    AuthError,
    CSRF_COOKIE_NAME,
    create_session_cookie,
    require_verified_university_email,
    validate_csrf,
    verify_request_session,
)
from workbench.settings import email_allowed


class FakeAuth:
    def __init__(self, claims: dict, cookie: str = "session-cookie"):
        self.claims = claims
        self.cookie = cookie

    def verify_id_token(self, id_token: str) -> dict:
        if id_token != "good-id-token":
            raise ValueError("invalid id token")
        return self.claims

    def create_session_cookie(self, id_token: str, expires_in) -> str:
        assert id_token == "good-id-token"
        assert expires_in is not None
        return self.cookie

    def verify_session_cookie(self, session_cookie: str, check_revoked: bool = True) -> dict:
        if session_cookie != self.cookie:
            raise ValueError("invalid session")
        return self.claims


def _claims(*, email: str, verified: bool, auth_time: float | None = None) -> dict:
    return {
        "email": email,
        "email_verified": verified,
        "uid": "user-1",
        "sub": "user-1",
        "auth_time": int(time.time() if auth_time is None else auth_time),
    }


def test_unverified_email_rejected(monkeypatch):
    monkeypatch.setenv("WORKBENCH_ALLOWED_DOMAINS", "utexas.edu")
    claims = _claims(email="analyst@utexas.edu", verified=False)
    with pytest.raises(AuthError, match="not verified"):
        require_verified_university_email(claims)
    with pytest.raises(AuthError, match="not verified"):
        create_session_cookie("good-id-token", auth_client=FakeAuth(claims))


def test_disallowed_domain(monkeypatch):
    monkeypatch.setenv("WORKBENCH_ALLOWED_DOMAINS", "utexas.edu")
    assert email_allowed("analyst@gmail.com") is False
    claims = _claims(email="analyst@gmail.com", verified=True)
    with pytest.raises(AuthError, match="domain is not allowed"):
        require_verified_university_email(claims)


def test_suffix_lookalike_rejected(monkeypatch):
    monkeypatch.setenv("WORKBENCH_ALLOWED_DOMAINS", "utexas.edu")
    assert email_allowed("user@utexas.edu.evil.com") is False
    assert email_allowed("user@notutexas.edu") is False
    assert email_allowed("user@evil.utexas.edu") is False
    claims = _claims(email="user@utexas.edu.evil.com", verified=True)
    with pytest.raises(AuthError, match="domain is not allowed"):
        require_verified_university_email(claims)


def test_exact_utexas_edu_allowed(monkeypatch):
    monkeypatch.setenv("WORKBENCH_ALLOWED_DOMAINS", "utexas.edu")
    assert email_allowed("analyst@utexas.edu") is True
    assert email_allowed("ANALYST@UTEXAS.EDU") is True
    claims = _claims(email="analyst@utexas.edu", verified=True)
    assert require_verified_university_email(claims) == "analyst@utexas.edu"
    cookie = create_session_cookie("good-id-token", auth_client=FakeAuth(claims))
    assert cookie == "session-cookie"


def test_csrf_mismatch():
    request = SimpleNamespace(cookies={CSRF_COOKIE_NAME: "expected-token"}, headers={})
    with pytest.raises(AuthError, match="CSRF mismatch"):
        validate_csrf(request, token="other-token")
    with pytest.raises(AuthError, match="CSRF mismatch"):
        validate_csrf(SimpleNamespace(cookies={}, headers={}), token="expected-token")
    validate_csrf(request, token="expected-token")


def test_verify_request_session_uses_cookie(monkeypatch):
    monkeypatch.setenv("WORKBENCH_ALLOWED_DOMAINS", "utexas.edu")
    monkeypatch.setenv("WORKBENCH_SESSION_COOKIE", "session")
    claims = _claims(email="analyst@utexas.edu", verified=True)
    request = SimpleNamespace(cookies={"session": "session-cookie"}, headers={})
    assert verify_request_session(request, auth_client=FakeAuth(claims))["email"] == "analyst@utexas.edu"
    with pytest.raises(AuthError, match="Not signed in"):
        verify_request_session(SimpleNamespace(cookies={}, headers={}), auth_client=FakeAuth(claims))


def _load_script(name: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_bootstrap_refuses_local_backend_without_flag(monkeypatch):
    monkeypatch.setenv("WORKBENCH_BACKEND", "local")
    monkeypatch.setenv("WORKBENCH_ALLOWED_DOMAINS", "utexas.edu")
    module = _load_script("bootstrap_admin")
    with pytest.raises(RuntimeError, match="Refusing to bootstrap"):
        module.bootstrap_admin("uid-1", "admin@utexas.edu")


def test_bootstrap_admin_allow_local_uses_injected_repository(monkeypatch):
    monkeypatch.setenv("WORKBENCH_BACKEND", "local")
    monkeypatch.setenv("WORKBENCH_ALLOWED_DOMAINS", "utexas.edu")
    from workbench.cloud.repository import CloudRepository
    from workbench.cloud.store import MemoryStore

    store = MemoryStore()
    repo = CloudRepository(store)
    module = _load_script("bootstrap_admin")
    module.bootstrap_admin("uid-1", "admin@utexas.edu", allow_local=True, repository=repo)
    assert store.get("members/uid-1")["role"] == "admin"
    assert store.get("system/admins")["uids"] == ["uid-1"]


def test_restore_plan_is_dry_run_and_keeps_access_closed():
    module = _load_script("recovery_restore")
    plan = module.plan_restore(
        {
            "status": "COMPLETE",
            "restore_access": "closed",
            "password_policy": "reset-required",
            "identities_sha256": "abc",
            "firestore_export": "gs://recovery/firestore",
        }
    )
    assert plan["dry_run"] is True
    assert plan["restore_access"] == "closed"
    assert plan["password_policy"] == "reset-required"
    assert "Do not reopen production" in " ".join(plan["smoke_test"])
