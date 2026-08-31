from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("WORKBENCH_DATA_DIR", ROOT / "data" / "workbench"))
DATABASE_URL = os.environ.get("WORKBENCH_DATABASE_URL", f"sqlite:///{DATA_DIR / 'workbench.db'}")
STORAGE_DIR = DATA_DIR / "storage"
RUNS_DIR = DATA_DIR / "runs"
EXPORTS_DIR = DATA_DIR / "exports"
SAMPLE_RATE = float(os.environ.get("WORKBENCH_QC_SAMPLE_RATE", "0.10"))
MIN_CALIBRATION_SAMPLES = int(os.environ.get("WORKBENCH_MIN_CALIBRATION_SAMPLES", "30"))
PRECISION_TARGET = float(os.environ.get("WORKBENCH_PRECISION_TARGET", "0.98"))


def ensure_directories() -> None:
    for path in (DATA_DIR, STORAGE_DIR, RUNS_DIR, EXPORTS_DIR):
        path.mkdir(parents=True, exist_ok=True)
