"""Server-only Supabase clients. Never share a mutable Auth session across users."""
import os


def create_admin_client():
    from supabase import ClientOptions, create_client

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
    return create_client(url, key, options=ClientOptions(auto_refresh_token=False, persist_session=False))
