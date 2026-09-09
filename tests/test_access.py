from fastapi import HTTPException
from starlette.requests import Request

from workbench.access import (LocalPrincipal, firebase_mode, reviewer_identity,
                              wants_html)


def test_local_mode_is_the_default(monkeypatch):
    monkeypatch.delenv("WORKBENCH_BACKEND", raising=False)
    assert firebase_mode() is False


def test_reviewer_identity_uses_submitted_name_locally():
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.principal = None
    assert reviewer_identity(request, " DK ") == "DK"


def test_reviewer_identity_rejects_blank_local_name():
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.principal = None
    try:
        reviewer_identity(request, "  ")
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("blank reviewer must fail locally")


def test_reviewer_identity_prefers_verified_session_email():
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.principal = LocalPrincipal(uid="abc", email="reviewer@utexas.edu", role="member")
    assert reviewer_identity(request, "forged-name") == "reviewer@utexas.edu"


def test_html_get_is_treated_as_browser_navigation():
    request = Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": [(b"accept", b"text/html")],
    })
    assert wants_html(request) is True
