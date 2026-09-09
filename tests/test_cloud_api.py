import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from workbench.cloud.api import get_actor, get_repository, get_storage_service, router
from workbench.cloud.repository import CloudRepository, Principal
from workbench.cloud.store import MemoryStore


class FakeStorage:
    def upload_original(self, **_kwargs):
        return SimpleNamespace(
            sha256="a" * 64, generation="7", content_type="image/png", size=3,
        )

    def authorized_download(self, page):
        assert page["generation"] == "7"
        return b"png"


def _client(storage=None):
    store = MemoryStore()
    repository = CloudRepository(store)
    repository.bootstrap_admin("admin", "admin@utexas.edu")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_storage_service] = lambda: storage or FakeStorage()
    app.dependency_overrides[get_actor] = lambda: Principal(
        "admin", "admin@utexas.edu", "admin",
    )
    return TestClient(app), store


def test_shared_batch_command_is_idempotent():
    client, store = _client()
    command = {
        "operation_id": "operation-create-1",
        "batch_id": "batch-1",
        "metadata": {
            "year": 1950,
            "schedule_type": "population",
            "district": "11-2A",
            "name": "Bastrop 11-2A",
            "schema_version": 1,
        },
    }
    first = client.post("/api/shared/batches", json=command)
    second = client.post("/api/shared/batches", json=command)
    assert first.status_code == 200
    assert second.json() == first.json()
    assert store.get("batches/batch-1")["district"] == "11-2A"


def test_shared_command_rejects_changed_payload_with_reused_id():
    client, _ = _client()
    command = {
        "operation_id": "operation-create-1",
        "batch_id": "batch-1",
        "metadata": {
            "year": 1950,
            "schedule_type": "population",
            "district": "11-2A",
            "name": "First",
            "schema_version": 1,
        },
    }
    assert client.post("/api/shared/batches", json=command).status_code == 200
    command["metadata"]["name"] = "Changed"
    conflict = client.post("/api/shared/batches", json=command)
    assert conflict.status_code == 409
    assert "different data" in conflict.json()["detail"]


def test_upload_registers_immutable_version_and_private_media():
    client, _ = _client()
    command = {
        "operation_id": "operation-create-1",
        "batch_id": "batch-1",
        "metadata": {
            "year": 1950,
            "schedule_type": "population",
            "district": "11-2A",
            "name": "Bastrop 11-2A",
            "schema_version": 1,
        },
    }
    assert client.post("/api/shared/batches", json=command).status_code == 200
    source = {
        "image_id": "ancestry-sheet-1",
        "year": 1950,
        "schedule_type": "population",
        "district": "11-2A",
    }
    uploaded = client.post(
        "/api/shared/pages/upload",
        data={
            "batch_id": "batch-1",
            "source_json": json.dumps(source),
            "operation_id": "operation-upload-1",
        },
        files={"image": ("sheet.png", b"png", "image/png")},
    )
    assert uploaded.status_code == 200
    page = uploaded.json()
    assert page["content_type"] == "image/png"
    assert page["generation"] == "7"
    media = client.get(f"/api/shared/pages/{page['id']}/media")
    assert media.status_code == 200
    assert media.content == b"png"
