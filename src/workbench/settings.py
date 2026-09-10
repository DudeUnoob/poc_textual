"""Workbench runtime settings. Secrets are server-only and never included in browser configuration."""
from __future__ import annotations

import os

DEFAULT_BACKEND = "local"
DEFAULT_DOMAIN = "utexas.edu,eid.utexas.edu,my.utexas.edu"
DEFAULT_SESSION_COOKIE = "session"
DEFAULT_ORIGIN = "http://localhost:8000"



def backend() -> str:
    value = os.environ.get("WORKBENCH_BACKEND", DEFAULT_BACKEND).strip().lower()
    value = value or DEFAULT_BACKEND
    if value not in {"local", "supabase"}:
        raise ValueError("WORKBENCH_BACKEND must be local or supabase")
    return value


def is_supabase() -> bool:
    return backend() == "supabase"


def allowed_domains() -> tuple[str, ...]:
    raw = os.environ.get("WORKBENCH_ALLOWED_DOMAINS", DEFAULT_DOMAIN)
    domains = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    return domains or tuple(DEFAULT_DOMAIN.split(","))


def allowed_emails() -> tuple[str, ...]:
    """Explicit account exceptions; these never confer an administrator role."""
    return tuple(value.strip().casefold() for value in
                 os.environ.get("WORKBENCH_ALLOWED_EMAILS", "").split(",") if value.strip())


def email_allowed(email: str) -> bool:
    """Exact domain match only. Rejects suffix lookalikes and extra subdomains."""
    if not isinstance(email, str):
        return False
    trimmed = email.strip()
    if trimmed.count("@") != 1:
        return False
    local, domain = trimmed.split("@", 1)
    if not local or not domain or any(char.isspace() for char in trimmed):
        return False
    return trimmed.casefold() in allowed_emails() or domain.lower() in allowed_domains()


def session_cookie_name() -> str:
    value = os.environ.get("WORKBENCH_SESSION_COOKIE", DEFAULT_SESSION_COOKIE).strip()
    return value or DEFAULT_SESSION_COOKIE


def public_origin() -> str:
    return os.environ.get("WORKBENCH_PUBLIC_ORIGIN", DEFAULT_ORIGIN).rstrip("/")


def cookies_secure() -> bool:
    return public_origin().startswith("https://")


def supabase_url() -> str:
    return os.environ.get("SUPABASE_URL", "https://ynsutdkbpzrgfetnrxax.supabase.co").strip().rstrip("/")


def supabase_storage_bucket() -> str:
    return os.environ.get("SUPABASE_STORAGE_BUCKET", "census-media").strip()


def supabase_web_config() -> dict[str, str]:
    """Only the publishable key may cross the browser boundary."""
    return {
        "url": supabase_url(),
        "publishableKey": os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip(),
    }
