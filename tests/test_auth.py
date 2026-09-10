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


def _token(claims):
    import base64
    import json
    payload = {"sub": claims["uid"], "iat": claims["auth_time"],
               "exp": int(time.time()) + 3600, "session_id": "session-1", "email": claims["email"],
               "amr": claims.get("amr", [{"method":"password", "timestamp":claims["auth_time"]}])}
    return "header." + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=") + ".signature"


class FakeAuth:
    def __init__(self, claims: dict, active=True):
        self.claims = claims
        self.auth = self
        self.active = active

    def get_user(self, token):
        if token != _token(self.claims):
            raise ValueError("Invalid token")
        return SimpleNamespace(user=SimpleNamespace(id=self.claims["uid"], email=self.claims["email"],
            email_confirmed_at="2026-01-01" if self.claims["email_verified"] else None))

    def rpc(self, name, args):
        assert name == "workbench_session_active"
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.active))


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
        create_session_cookie(_token(claims), auth_client=FakeAuth(claims))


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
    cookie = create_session_cookie(_token(claims), auth_client=FakeAuth(claims))
    assert cookie == _token(claims)


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
    request = SimpleNamespace(cookies={"session": _token(claims)}, headers={})
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
            "provider": "supabase",
            "database_sha256": "def",
            "database_export": "recovery/database.dump",
        }
    )
    assert plan["dry_run"] is True
    assert plan["restore_access"] == "closed"
    assert plan["password_policy"] == "reset-required"
    assert "Do not reopen production" in " ".join(plan["smoke_test"])


def test_revoked_supabase_session_rejected():
    claims = _claims(email="analyst@utexas.edu", verified=True)
    with pytest.raises(AuthError, match="revoked"):
        create_session_cookie(_token(claims), auth_client=FakeAuth(claims, active=False))


def test_old_access_token_cannot_establish_cookie():
    claims = _claims(email="analyst@utexas.edu", verified=True, auth_time=time.time()-600)
    with pytest.raises(AuthError, match="Recent sign-in"):
        create_session_cookie(_token(claims), auth_client=FakeAuth(claims))


def test_live_email_change_rejected_even_when_cookie_claims_were_eligible():
    claims = _claims(email="analyst@utexas.edu", verified=True)
    client = FakeAuth(claims)
    original = client.get_user
    def changed(token):
        response = original(token)
        response.user.email = "analyst@example.com"
        return response
    client.get_user = changed
    with pytest.raises(AuthError, match="Email changed"):
        create_session_cookie(_token(claims), auth_client=client)


def test_banned_user_fails_even_if_provider_accepts_jwt():
    claims = _claims(email="analyst@utexas.edu", verified=True)
    client = FakeAuth(claims)
    original = client.get_user
    def banned(token):
        response = original(token)
        response.user.banned_until = "2099-01-01T00:00:00Z"
        return response
    client.get_user = banned
    with pytest.raises(AuthError, match="disabled"):
        create_session_cookie(_token(claims), auth_client=client)


@pytest.mark.parametrize("amr", [[], [{"method":"otp", "timestamp":int(time.time())}],
    [{"method":"password", "timestamp":int(time.time())-600}]])
def test_refreshed_token_requires_recent_password_not_recent_issuance(amr):
    claims = _claims(email="analyst@utexas.edu", verified=True)
    claims["amr"] = amr
    with pytest.raises(AuthError, match="Recent sign-in"):
        create_session_cookie(_token(claims), auth_client=FakeAuth(claims))


def test_explicit_personal_account_and_university_subdomains(monkeypatch):
    monkeypatch.setenv('WORKBENCH_ALLOWED_DOMAINS', 'utexas.edu,eid.utexas.edu,my.utexas.edu')
    monkeypatch.setenv('WORKBENCH_ALLOWED_EMAILS', 'to.baladev@gmail.com')
    for email in ['to.baladev@gmail.com', 'TO.BALADEV@GMAIL.COM', 'a@utexas.edu', 'a@eid.utexas.edu', 'a@my.utexas.edu']:
        assert email_allowed(email)
    for email in ['someone@gmail.com', 'to.baladev+extra@gmail.com', 'a@evil.eid.utexas.edu', 'a@my.utexas.edu.evil.com', 'a b@utexas.edu']:
        assert not email_allowed(email)
