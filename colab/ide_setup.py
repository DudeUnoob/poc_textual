
"""Helpers for running census OCR on Colab via IDE extension (no web upload UI)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def find_project_root(explicit: str | Path | None = None) -> Path:
    """Locate poc_textual root on Colab cloud or local synced workspace."""
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if _is_project_root(p):
            return p
        raise FileNotFoundError(f"Not a project root: {p}")

    candidates: list[Path] = []
    for base in (Path.cwd(), Path("/content"), Path("/content/poc_textual")):
        if base.exists():
            candidates.append(base.resolve())
            candidates.extend(p.parent for p in base.rglob("CLAUDE.md") if p.parent.name != "venv")

    seen: set[Path] = set()
    for p in candidates:
        if p in seen:
            continue
        seen.add(p)
        if _is_project_root(p):
            return p.resolve()

    raise FileNotFoundError(
        "Could not find poc_textual project root. "
        "Set PROJECT_DIR explicitly in the config cell, e.g. "
        "Path('/content/poc_textual') or your synced workspace path."
    )


def _is_project_root(p: Path) -> bool:
    return (
        (p / "CLAUDE.md").is_file()
        and (p / "src" / "compare.py").is_file()
        and (p / "data" / "ground_truth").is_dir()
    )


def setup_paths(project_dir: Path) -> dict[str, Path]:
    return {
        "project": project_dir,
        "gt": project_dir / "data/ground_truth/Bastrop County 1950 Clean.xlsx",
        "images": project_dir / "data/raw_images/1950_11-1",
        "outputs": project_dir / "data/outputs/1950_11-1",
        "results": project_dir / "results/colab_ide",
        "prompts": project_dir / "prompts",
        "src": project_dir / "src",
    }


def setup_hf_auth(project_dir: Path) -> None:
    """Load HF_TOKEN from .env or environment — no Colab secrets UI needed."""
    try:
        from dotenv import load_dotenv
        load_dotenv(project_dir / ".env")
    except ImportError:
        pass

    token = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or os.environ.get("HUGGINGFACE_HUB_TOKEN")
    )
    if not token:
        raise RuntimeError(
            "HF_TOKEN not set. Add to project .env:\n  HF_TOKEN=hf_...\n"
            "(copy from .env.example)"
        )

    from huggingface_hub import login
    login(token=token, add_to_git_credential=False)
    os.environ["HF_TOKEN"] = token
    print("Hugging Face: authenticated via HF_TOKEN from .env / environment")


def add_src_to_path(src_dir: Path) -> None:
    s = str(src_dir)
    if s not in sys.path:
        sys.path.insert(0, s)
