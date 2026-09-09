#!/usr/bin/env python3
"""Operator-only first-admin bootstrap. Never expose as an HTTP route.

Uses CloudRepository.bootstrap_admin. Refuses to run unless
WORKBENCH_BACKEND=firebase or --allow-local is passed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def assert_bootstrap_allowed(*, allow_local: bool = False) -> None:
    from workbench.settings import is_firebase

    if not is_firebase() and not allow_local:
        raise RuntimeError(
            "Refusing to bootstrap unless WORKBENCH_BACKEND=firebase or --allow-local"
        )


def live_repository():
    from firebase_admin import firestore

    from workbench.auth import init_firebase
    from workbench.cloud.repository import CloudRepository
    from workbench.cloud.store import FirebaseStore

    init_firebase()
    return CloudRepository(FirebaseStore(firestore.client()))


def bootstrap_admin(uid: str, email: str, *, allow_local: bool = False, repository=None) -> None:
    from workbench.settings import email_allowed

    assert_bootstrap_allowed(allow_local=allow_local)
    if not str(uid).strip() or not str(email).strip():
        raise ValueError("uid and email are required")
    if not email_allowed(email):
        raise ValueError("Email domain is not allowed")
    repo = repository if repository is not None else live_repository()
    repo.bootstrap_admin(str(uid).strip(), str(email).strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--allow-local", action="store_true")
    args = parser.parse_args(argv)
    bootstrap_admin(args.uid, args.email, allow_local=args.allow_local)
    print("First administrator recorded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
