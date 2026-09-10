"""Build immutable research exports from a finalized Supabase Postgres release."""
from __future__ import annotations

import csv
from io import BytesIO, StringIO
import json
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook

from census_schemas import get_census_schema
from .repository import CloudError, CloudRepository


def release_rows(repository: CloudRepository, release_id: str) -> tuple[dict, list[dict]]:
    release = repository.get("releases", release_id)
    if not release:
        raise CloudError("Release not found", 404)
    batch = repository.get("batches", release["batch_id"])
    if not batch:
        raise CloudError("Release batch not found", 404)
    snapshots = repository.store.list(f"releases/{release_id}/pages")
    records: list[dict] = []
    audit: list[dict] = []
    for snapshot in sorted(snapshots, key=lambda item: item.get("id", "")):
        page = snapshot["page"]
        for row in sorted(snapshot["rows"], key=lambda item: str(item["row_key"])):
            record = dict(row["values"])
            records.append(record)
            audit.append({
                "release_id": release_id,
                "batch_id": batch["id"],
                "page_id": page["id"],
                "source_id": page.get("source_id"),
                "source": page.get("source"),
                "source_generation": page.get("generation"),
                "source_checksum": page.get("checksum"),
                "row_id": row["id"],
                "row_key": row["row_key"],
                "reading_states": row.get("reading_states", {}),
                "revision": row.get("revision"),
                "reviewed_by": row.get("reviewed_by"),
                "reviewed_at": row.get("reviewed_at"),
                "qa_by": row.get("qa_by"),
                "job_id": row.get("job_id"),
                "excluded": bool(row.get("excluded")),
                "exclusion_reason": row.get("exclusion_reason"),
                "excluded_by": row.get("excluded_by"),
            })
    return batch, [{"values": row, "audit": history} for row, history in zip(records, audit)]


def build_release_zip(repository: CloudRepository, release_id: str) -> bytes:
    batch, rows = release_rows(repository, release_id)
    schema = get_census_schema(
        int(batch["year"]),
        str(batch["schedule_type"]),
        batch.get("sheet_name"),
    )
    columns = list(schema.columns)
    values = [
        {column: item["values"].get(column) for column in columns}
        for item in rows
        if not item["audit"]["excluded"]
    ]
    provenance = {
        "release_id": release_id,
        "batch": batch,
        "schema": {
            "year": schema.year,
            "schedule_type": schema.schedule_type,
            "sheet_name": schema.sheet_name,
            "catalog_version": schema.catalog_version,
            "columns": columns,
        },
        "rows": [item["audit"] for item in rows],
    }

    csv_buffer = StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=columns)
    writer.writeheader()
    writer.writerows(values)

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Reviewed Records"
    worksheet.append(columns)
    for row in values:
        worksheet.append([row[column] for column in columns])
    xlsx_buffer = BytesIO()
    workbook.save(xlsx_buffer)

    archive = BytesIO()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
        bundle.writestr("reviewed_records.csv", csv_buffer.getvalue())
        bundle.writestr("reviewed_records.json", json.dumps(values, indent=2, default=str))
        bundle.writestr("reviewed_records.xlsx", xlsx_buffer.getvalue())
        bundle.writestr("audit.json", json.dumps(provenance, indent=2, default=str))
    return archive.getvalue()
