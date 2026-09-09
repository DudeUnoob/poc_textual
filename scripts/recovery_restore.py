#!/usr/bin/env python3
"""Documented restore from a COMPLETE recovery set.

Default is dry-run / checklist only. This module does not wipe production,
does not delete Firestore, and does not reopen the portal.

Restore always leaves restore_access=closed and password_policy=reset-required.
Passwords are reset on restore; operators reopen access only after smoke tests.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

CLOSED_ACCESS = {"closed": True, "restore_access": "closed", "password_policy": "reset-required"}


def restore_note(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate COMPLETE.json constraints. Does not mutate any system."""
    errors: list[str] = []
    if manifest.get("status") != "COMPLETE":
        errors.append("Manifest is not COMPLETE")
    if manifest.get("restore_access") != "closed":
        errors.append("restore_access must be closed")
    if manifest.get("password_policy") != "reset-required":
        errors.append("password_policy must be reset-required")
    if not manifest.get("identities_sha256"):
        errors.append("identities_sha256 missing")
    if not manifest.get("firestore_export"):
        errors.append("firestore_export missing")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "restore_access": "closed",
        "password_policy": "reset-required",
        "snapshot_time": manifest.get("snapshot_time"),
        "firestore_export": manifest.get("firestore_export"),
        "identities": manifest.get("identities"),
        "identities_sha256": manifest.get("identities_sha256"),
        "note": "Keep the portal closed until smoke tests pass. Require password reset for every restored account.",
    }


def close_access(store: Any | None = None, *, dry_run: bool = True) -> dict[str, Any]:
    """Close the portal before any restore write."""
    planned = {"path": "system/access", "value": dict(CLOSED_ACCESS), "dry_run": dry_run}
    if dry_run or store is None:
        return planned

    def action(tx):
        current = tx.get("system/access") or {}
        tx.set("system/access", dict(current, **CLOSED_ACCESS))
        return tx.get("system/access")

    return {"dry_run": False, "result": store.atomic(action)}


def clear_leases(store: Any | None = None, *, dry_run: bool = True) -> dict[str, Any]:
    """Drop row edit leases so restored browsers cannot keep writing."""
    planned = {"action": "Set lease=null on every rows/* document", "dry_run": dry_run}
    if dry_run or store is None:
        return planned
    rows = store.list("rows")

    def action(tx):
        cleared = 0
        for row in rows:
            current = tx.get(f"rows/{row['id']}")
            if current and current.get("lease"):
                tx.set(f"rows/{row['id']}", dict(current, lease=None))
                cleared += 1
        return cleared

    return {"dry_run": False, "cleared": store.atomic(action)}


def invalidate_sessions(
    identities: list[dict[str, Any]] | None = None,
    *,
    auth_client: Any | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Revoke Firebase refresh tokens. Does not print identity details."""
    uids = [item["uid"] for item in identities or [] if item.get("uid")]
    planned = {
        "action": "revoke_refresh_tokens for every restored UID",
        "count": len(uids),
        "password_policy": "reset-required",
        "dry_run": dry_run,
    }
    if dry_run or auth_client is None:
        return planned
    for uid in uids:
        auth_client.revoke_refresh_tokens(uid)
    return {"dry_run": False, "revoked": len(uids), "password_policy": "reset-required"}


def smoke_test_checklist() -> list[str]:
    return [
        "Portal remains closed (system/access.closed, restore_access=closed)",
        "Pre-restore session cookies no longer verify",
        "Password reset is required before any restored user can sign in",
        "Verified allowed-domain email can sign in only after access is reopened",
        "Unverified email cannot mint a session cookie",
        "Disallowed domains and suffix lookalikes are rejected",
        "Browser cannot read or write Firestore or Storage",
        "A sample authorized row save succeeds after reopen",
        "Do not reopen production until this checklist passes",
    ]


def plan_restore(manifest: dict[str, Any], *, dry_run: bool = True) -> dict[str, Any]:
    note = restore_note(manifest)
    return {
        "dry_run": dry_run,
        "restore_access": "closed",
        "password_policy": "reset-required",
        "steps": [
            close_access(dry_run=True),
            {"action": "Import Firestore PITR export", "uri": note["firestore_export"]},
            {"action": "Copy versioned Storage objects from the recovery prefix"},
            clear_leases(dry_run=True),
            invalidate_sessions(dry_run=True),
            note,
        ],
        "smoke_test": smoke_test_checklist(),
        "warning": "Dry-run only unless an operator later applies a reviewed runbook. This tool does not wipe production or reopen access.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", help="Path to COMPLETE.json from the recovery bucket")
    args = parser.parse_args(argv)
    if args.manifest:
        manifest = json.loads(Path(args.manifest).read_text())
    else:
        manifest = {
            "status": "COMPLETE",
            "restore_access": "closed",
            "password_policy": "reset-required",
            "identities_sha256": "dry-run",
            "firestore_export": "gs://example/recovery/firestore",
        }
    print(json.dumps(plan_restore(manifest, dry_run=True), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
