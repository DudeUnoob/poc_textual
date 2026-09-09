from io import BytesIO
import json
from zipfile import ZipFile

from workbench.cloud.exports import build_release_zip
from workbench.cloud.repository import CloudRepository
from workbench.cloud.store import MemoryStore


def test_release_export_preserves_workbook_schema_and_provenance():
    store = MemoryStore()
    store.documents["batches/batch-1"] = {
        "id": "batch-1",
        "year": 1950,
        "schedule_type": "population",
        "sheet_name": "Bastrop 11-2A",
    }
    store.documents["releases/release-1"] = {
        "id": "release-1", "batch_id": "batch-1", "page_ids": ["page-1"],
    }
    store.documents["releases/release-1/pages/page-1"] = {
        "page": {
            "id": "page-1", "source_id": "source-1",
            "source": {"image_id": "ancestry-1"},
            "generation": "7", "checksum": "a" * 64,
        },
        "rows": [{
            "id": "row-1", "row_key": "1",
            "values": {"Surname": "Lewis", "Given Name": "Jasper H"},
            "reading_states": {"Surname": "value", "Given Name": "value"},
            "revision": 2, "reviewed_by": "reviewer-1", "qa_by": "reviewer-2",
            "job_id": "job-1",
        }],
    }

    payload = build_release_zip(CloudRepository(store), "release-1")
    with ZipFile(BytesIO(payload)) as archive:
        assert set(archive.namelist()) == {
            "reviewed_records.csv", "reviewed_records.json",
            "reviewed_records.xlsx", "audit.json",
        }
        records = json.loads(archive.read("reviewed_records.json"))
        audit = json.loads(archive.read("audit.json"))
    assert records[0]["Surname"] == "Lewis"
    assert audit["schema"]["sheet_name"] == "Bastrop 11-2A"
    assert audit["rows"][0]["source_generation"] == "7"
    assert audit["rows"][0]["qa_by"] == "reviewer-2"
