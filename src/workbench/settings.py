"""Workbench runtime settings. Service-account paths are never hardcoded."""
from __future__ import annotations

import os

DEFAULT_BACKEND = "local"
DEFAULT_DOMAIN = "utexas.edu"
DEFAULT_SESSION_COOKIE = "session"
DEFAULT_ORIGIN = "http://localhost:8000"

# GOOGLE_APPLICATION_CREDENTIALS is optional and read only by Google ADC.
# Do not assign, log, or embed it here.


def backend() -> str:
    value = os.environ.get("WORKBENCH_BACKEND", DEFAULT_BACKEND).strip().lower()
    return value or DEFAULT_BACKEND


def is_firebase() -> bool:
    return backend() == "firebase"


def allowed_domains() -> tuple[str, ...]:
    raw = os.environ.get("WORKBENCH_ALLOWED_DOMAINS", DEFAULT_DOMAIN)
    domains = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    return domains or (DEFAULT_DOMAIN,)


def email_allowed(email: str) -> bool:
    """Exact domain match only. Rejects suffix lookalikes and extra subdomains."""
    if not isinstance(email, str):
        return False
    trimmed = email.strip()
    if trimmed.count("@") != 1:
        return False
    local, domain = trimmed.split("@", 1)
    if not local or not domain:
        return False
    return domain.lower() in allowed_domains()


def session_cookie_name() -> str:
    value = os.environ.get("WORKBENCH_SESSION_COOKIE", DEFAULT_SESSION_COOKIE).strip()
    return value or DEFAULT_SESSION_COOKIE


def public_origin() -> str:
    return os.environ.get("WORKBENCH_PUBLIC_ORIGIN", DEFAULT_ORIGIN).rstrip("/")


def cookies_secure() -> bool:
    return public_origin().startswith("https://")


def firebase_project_id() -> str:
    return os.environ.get("FIREBASE_PROJECT_ID", "").strip()


def firebase_storage_bucket() -> str:
    return os.environ.get("FIREBASE_STORAGE_BUCKET", "").strip()


def firebase_web_config() -> dict[str, str]:
    """Browser Auth config only. Never includes a service account."""
    return {
        "apiKey": os.environ.get("FIREBASE_API_KEY", "").strip(),
        "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN", "").strip(),
        "projectId": firebase_project_id(),
        "appId": os.environ.get("FIREBASE_APP_ID", "").strip(),
        "storageBucket": firebase_storage_bucket(),
    }
