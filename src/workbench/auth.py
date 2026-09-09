"""Firebase Auth session cookies, domain membership, and CSRF helpers."""
from __future__ import annotations

import hmac
import secrets
import time
from datetime import timedelta
from typing import Any

from workbench.cloud.repository import Principal
from workbench.settings import (
    cookies_secure,
    email_allowed,
    firebase_project_id,
    firebase_storage_bucket,
    is_firebase,
    session_cookie_name,
)

SESSION_EXPIRES = timedelta(days=5)
RECENT_SIGN_IN_SECONDS = 5 * 60
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER = "x-csrf-token"
CSRF_FIELD = "csrf_token"

_firebase_app: Any = None


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def init_firebase() -> Any:
    """Lazy Admin SDK init via Application Default Credentials."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app
    import firebase_admin
    from firebase_admin import credentials

    options: dict[str, str] = {}
    project_id = firebase_project_id()
    bucket = firebase_storage_bucket()
    if project_id:
        options["projectId"] = project_id
    if bucket:
        options["storageBucket"] = bucket
    try:
        _firebase_app = firebase_admin.get_app()
    except ValueError:
        _firebase_app = firebase_admin.initialize_app(
            credentials.ApplicationDefault(),
            options or None,
        )
    return _firebase_app


def _auth_module() -> Any:
    init_firebase()
    from firebase_admin import auth

    return auth


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
    client = auth_client or _auth_module()
    try:
        claims = client.verify_id_token(id_token)
    except AuthError:
        raise
    except Exception as exc:
        raise AuthError("Invalid ID token", 401) from exc
    require_verified_university_email(claims)
    auth_time = int(claims.get("auth_time") or 0)
    clock = time.time() if now is None else now
    if clock - auth_time > RECENT_SIGN_IN_SECONDS:
        raise AuthError("Recent sign-in required", 401)
    try:
        cookie = client.create_session_cookie(id_token, expires_in=expires_in)
    except Exception as exc:
        raise AuthError("Could not create session", 401) from exc
    if isinstance(cookie, bytes):
        return cookie.decode()
    return str(cookie)


def verify_request_session(
    request: Any,
    *,
    auth_client: Any | None = None,
    check_revoked: bool = True,
) -> dict[str, Any]:
    cookie = (getattr(request, "cookies", None) or {}).get(session_cookie_name())
    if not cookie:
        raise AuthError("Not signed in", 401)
    client = auth_client or _auth_module()
    try:
        claims = client.verify_session_cookie(cookie, check_revoked=check_revoked)
    except Exception as exc:
        raise AuthError("Invalid session", 401) from exc
    require_verified_university_email(claims)
    return claims


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
    attach_session_cookie(response, cookie)
    existing = csrf_token(request)
    attach_csrf_cookie(response, existing or new_csrf_token())
    client = auth_client or _auth_module()
    claims = client.verify_id_token(id_token)
    uid, email = identity_from_claims(claims)
    if is_firebase():
        from firebase_admin import firestore as firebase_firestore
        from workbench.cloud.repository import CloudRepository
        from workbench.cloud.store import FirebaseStore

        CloudRepository(FirebaseStore(firebase_firestore.client())).ensure_member(uid, email)
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
    "init_firebase",
    "is_firebase",
    "new_csrf_token",
    "principal_from_claims",
    "require_verified_university_email",
    "session_cookie_params",
    "validate_csrf",
    "verify_request_session",
]
