from __future__ import annotations

from starlette.testclient import TestClient

from workbench.web import app


def test_health_and_dashboard_render_in_local_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_DATA_DIR", str(tmp_path / "workbench"))
    monkeypatch.setenv("WORKBENCH_BACKEND", "local")
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True
        home = client.get("/")
        assert home.status_code == 200
        assert "Census Review Workbench" in home.text
        assert "1850" in home.text
        sheets = client.get(
            "/api/workbook-sheets",
            params={"year": 1950, "schedule_type": "population"},
        )
        assert sheets.status_code == 200
        assert "Bastrop 11-2A" in sheets.json()["sheets"]
