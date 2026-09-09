"""Request identity for local and Firebase deployments.

Local mode keeps the existing unauthenticated workbench so unit tests and
single-operator use stay unchanged. Firebase mode requires a verified
university session on every protected route, including media and SSE.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

PUBLIC_PREFIXES = ("/static/",)
PUBLIC_PATHS = {"/login", "/session", "/logout", "/health", "/favicon.ico"}
CSRF_PUBLIC_POSTS = {"/session", "/logout"}


@dataclass(frozen=True)
class LocalPrincipal:
    uid: str = "local"
    email: str = "local-reviewer"
    role: str = "admin"


def firebase_mode() -> bool:
    from .settings import is_firebase

    return is_firebase()


def _http_error(exc) -> HTTPException:
    return HTTPException(status_code=getattr(exc, "status", 401), detail=str(exc))


def attach_principal(request: Request) -> None:
    request.state.principal = None
    if not firebase_mode():
        return
    from .auth import AuthError, principal_from_claims, verify_request_session

    try:
        claims = verify_request_session(request)
        principal = principal_from_claims(claims)
        from firebase_admin import firestore as firebase_firestore
        from .cloud.repository import CloudRepository
        from .cloud.store import FirebaseStore

        request.state.principal = CloudRepository(
            FirebaseStore(firebase_firestore.client())
        ).ensure_member(principal.uid, principal.email)
    except AuthError:
        request.state.principal = None
    except Exception:
        request.state.principal = None


async def submitted_csrf_token(request: Request) -> str | None:
    """Read CSRF from a header, or from a cached form body. Never parse JSON."""
    header = request.headers.get("x-csrf-token") or request.headers.get("X-CSRF-Token")
    if header:
        return header
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        return None
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        form = await request.form()
        token = form.get("csrf_token")
        return str(token) if token else None
    return None


def enforce(request: Request, csrf_token: str | None = None) -> Response | None:
    if not firebase_mode():
        return None
    path = request.url.path
    if path.startswith(PUBLIC_PREFIXES) or path == "/health" or path == "/favicon.ico":
        return None
    from .auth import AuthError, validate_csrf

    mutating = request.method in {"POST", "PUT", "PATCH", "DELETE"}
    if mutating and path in CSRF_PUBLIC_POSTS:
        try:
            validate_csrf(request, csrf_token)
        except AuthError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=exc.status)
        return None
    if path in PUBLIC_PATHS:
        return None
    if getattr(request.state, "principal", None) is not None:
        if mutating:
            try:
                validate_csrf(request, csrf_token)
            except AuthError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=exc.status)
        return None
    if wants_html(request):
        return RedirectResponse("/login", status_code=303)
    return JSONResponse({"detail": "Authentication required"}, status_code=401)


def wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or (
        request.method == "GET" and not path_is_data(request.url.path)
    )


def path_is_data(path: str) -> bool:
    return path.endswith("/status") or path.endswith("/events") or "/image" in path or "/crop" in path


def current_principal(request: Request):
    return getattr(request.state, "principal", None)


def require_principal(request: Request):
    principal = current_principal(request)
    if principal is not None:
        return principal
    if firebase_mode():
        raise HTTPException(status_code=401, detail="Authentication required")
    return LocalPrincipal()


def require_admin(request: Request):
    principal = require_principal(request)
    if firebase_mode() and getattr(principal, "role", None) != "admin":
        raise HTTPException(status_code=403, detail="Administrator required")
    return principal


def reviewer_identity(request: Request, submitted: str | None) -> str:
    principal = current_principal(request)
    if principal is not None:
        return principal.email
    name = (submitted or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Reviewer name or initials are required.")
    return name


def template_context(request: Request, extra: dict | None = None) -> dict:
    from .settings import allowed_domains, firebase_web_config

    context = dict(extra or {})
    config = firebase_web_config()
    token = ""
    if firebase_mode():
        from .auth import CSRF_COOKIE_NAME, new_csrf_token

        token = (
            request.cookies.get(CSRF_COOKIE_NAME)
            or getattr(request.state, "issued_csrf", None)
            or ""
        )
        if not token:
            token = new_csrf_token()
            request.state.issued_csrf = token
    context.setdefault("current_user", current_principal(request))
    context.setdefault("firebase_mode", firebase_mode())
    context.setdefault("firebase_auth", firebase_mode())
    context.setdefault("csrf_token", token)
    context.setdefault("firebase_config", config)
    context.setdefault("firebase_api_key", config.get("apiKey", ""))
    context.setdefault("firebase_auth_domain", config.get("authDomain", ""))
    context.setdefault("firebase_project_id", config.get("projectId", ""))
    context.setdefault("firebase_app_id", config.get("appId", ""))
    context.setdefault("allowed_domains", list(allowed_domains()))
    return context


def attach_response_cookies(request: Request, response: Response) -> Response:
    token = getattr(request.state, "issued_csrf", None)
    if token and firebase_mode():
        from .auth import attach_csrf_cookie

        attach_csrf_cookie(response, token)
    session_id = getattr(request.state, "issued_review_session", None)
    if session_id:
        from .settings import cookies_secure

        response.set_cookie(
            "review_session",
            session_id,
            httponly=True,
            secure=cookies_secure(),
            samesite="lax",
            path="/",
            max_age=60 * 60 * 12,
        )
    return response
