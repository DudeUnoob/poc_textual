"""Supabase verified access tokens in HttpOnly cookies, membership, and CSRF."""
from __future__ import annotations

import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from workbench.cloud.repository import Principal
from workbench.settings import cookies_secure, email_allowed, session_cookie_name
from workbench.supabase_client import create_admin_client
import base64
import json

SESSION_EXPIRES = timedelta(hours=1)
RECENT_SIGN_IN_SECONDS = 5 * 60
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER = "x-csrf-token"
CSRF_FIELD = "csrf_token"


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def _token_payload(token: str) -> dict:
    # Used only AFTER get_user verifies this exact token with Supabase Auth.
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception as exc:
        raise AuthError("Invalid access token") from exc


def verify_access_token(token: str, *, auth_client=None, now=None) -> dict:
    client = auth_client or create_admin_client()
    try:
        user = client.auth.get_user(token).user
        payload = _token_payload(token)
        clock = time.time() if now is None else now
        if not user or payload.get("sub") != str(user.id) or float(payload.get("exp", 0)) <= clock:
            raise AuthError("Expired or invalid session")
        banned_until = getattr(user, "banned_until", None)
        if banned_until:
            if isinstance(banned_until, str):
                banned_until = datetime.fromisoformat(banned_until.replace("Z", "+00:00"))
            if banned_until.tzinfo is None:
                banned_until = banned_until.replace(tzinfo=timezone.utc)
            if banned_until.timestamp() > clock:
                raise AuthError("Account disabled", 403)
        if str(payload.get("email") or "").casefold() != str(user.email or "").casefold():
            raise AuthError("Email changed; sign in again", 401)
        password_times = [int(item.get("timestamp", 0)) for item in payload.get("amr", [])
                          if item.get("method") == "password"]
        session_id = payload.get("session_id")
        if not session_id or client.rpc("workbench_session_active", {
            "p_session_id": session_id, "p_uid": str(user.id),
        }).execute().data is not True:
            raise AuthError("Session revoked")
        claims = {"uid": str(user.id), "sub": str(user.id), "email": user.email,
                  "email_verified": bool(user.email_confirmed_at),
                  "auth_time": max(password_times, default=0), "exp": payload["exp"],
                  "session_id": session_id}
        require_verified_university_email(claims)
        return claims
    except AuthError:
        raise
    except Exception as exc:
        raise AuthError("Invalid session") from exc


def require_verified_university_email(claims: dict[str, Any]) -> str:
    email = str((claims or {}).get("email") or "")
    if not (claims or {}).get("email_verified"):
        raise AuthError("Email is not verified", 403)
    if not email_allowed(email):
        raise AuthError("Email domain is not allowed", 403)
    return email


def identity_from_claims(claims: dict[str, Any]) -> tuple[str, str]:
    """UID and email from verified claims. Never reads a client-supplied role."""
    uid = (claims or {}).get("uid") or (claims or {}).get("user_id") or (claims or {}).get("sub")
    email = require_verified_university_email(claims)
    if not uid:
        raise AuthError("Session is missing identity", 401)
    return str(uid), email


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    """Identity only. Role is assigned later from membership, never from the client."""
    uid, email = identity_from_claims(claims)
    return Principal(uid=uid, email=email)


def create_session_cookie(
    id_token: str,
    *,
    auth_client: Any | None = None,
    expires_in: timedelta = SESSION_EXPIRES,
    now: float | None = None,
) -> str:
    if not isinstance(id_token, str) or not id_token.strip():
        raise AuthError("Missing ID token", 400)
    claims = verify_access_token(id_token, auth_client=auth_client, now=now)
    clock = time.time() if now is None else now
    age = clock - int(claims.get("auth_time") or 0)
    if age < -60 or age > RECENT_SIGN_IN_SECONDS:
        raise AuthError("Recent sign-in required", 401)
    return id_token


def verify_request_session(
    request: Any,
    *,
    auth_client: Any | None = None,
    check_revoked: bool = True,
) -> dict[str, Any]:
    cookie = (getattr(request, "cookies", None) or {}).get(session_cookie_name())
    if not cookie:
        raise AuthError("Not signed in", 401)
    # Always check revocation, even if a legacy caller supplies False.
    return verify_access_token(cookie, auth_client=auth_client)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _submitted_csrf(request: Any, token: str | None) -> str | None:
    if token:
        return token
    headers = getattr(request, "headers", None) or {}
    getter = getattr(headers, "get", None)
    if getter:
        return getter(CSRF_HEADER) or getter("X-CSRF-Token")
    return None


def validate_csrf(request: Any, token: str | None = None) -> None:
    expected = (getattr(request, "cookies", None) or {}).get(CSRF_COOKIE_NAME)
    provided = _submitted_csrf(request, token)
    if not expected or not provided or not hmac.compare_digest(str(expected), str(provided)):
        raise AuthError("CSRF mismatch", 403)


def session_cookie_params() -> dict[str, Any]:
    return {
        "key": session_cookie_name(),
        "max_age": int(SESSION_EXPIRES.total_seconds()),
        "httponly": True,
        "secure": cookies_secure(),
        "samesite": "lax",
        "path": "/",
    }


def csrf_cookie_params() -> dict[str, Any]:
    params = session_cookie_params()
    params["key"] = CSRF_COOKIE_NAME
    return params


def attach_session_cookie(response: Any, value: str) -> None:
    params = session_cookie_params()
    response.set_cookie(params.pop("key"), value, **params)


def attach_csrf_cookie(response: Any, token: str) -> None:
    params = csrf_cookie_params()
    response.set_cookie(params.pop("key"), token, **params)


def clear_session_cookies(response: Any) -> None:
    response.delete_cookie(session_cookie_name(), path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


def csrf_token(request: Any) -> str:
    return str((getattr(request, "cookies", None) or {}).get(CSRF_COOKIE_NAME) or "")


def establish_session(request: Any, response: Any, id_token: str, *, auth_client: Any | None = None) -> str:
    cookie = create_session_cookie(id_token, auth_client=auth_client)
    claims = verify_access_token(cookie, auth_client=auth_client)
    uid, email = identity_from_claims(claims)
    from workbench.settings import is_supabase
    if is_supabase():
        from workbench.cloud.repository import CloudRepository
        from workbench.cloud.store import SupabaseStore
        CloudRepository(SupabaseStore(create_admin_client())).ensure_member(uid, email)
    attach_session_cookie(response, cookie)
    attach_csrf_cookie(response, new_csrf_token())
    return cookie


def clear_session(response: Any) -> None:
    clear_session_cookies(response)


__all__ = [
    "AuthError",
    "CSRF_COOKIE_NAME",
    "CSRF_FIELD",
    "CSRF_HEADER",
    "Principal",
    "SESSION_EXPIRES",
    "attach_csrf_cookie",
    "attach_session_cookie",
    "clear_session",
    "clear_session_cookies",
    "create_session_cookie",
    "csrf_token",
    "establish_session",
    "csrf_cookie_params",
    "identity_from_claims",
    "new_csrf_token",
    "principal_from_claims",
    "require_verified_university_email",
    "session_cookie_params",
    "validate_csrf",
    "verify_request_session",
]
