"""Construct the real SDK client without making requests or using live secrets."""
from workbench.supabase_client import create_admin_client


def test_admin_client_supports_sync_sdk_and_isolates_sessions(monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', 'https://example.supabase.co')
    monkeypatch.setenv('SUPABASE_SECRET_KEY', 'sb_secret_test_only')
    first = create_admin_client()
    second = create_admin_client()
    assert first.options.persist_session is False
    assert first.options.auto_refresh_token is False
    assert first.options.storage is not second.options.storage
    assert first.auth is not second.auth
