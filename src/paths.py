"""Project root paths — import from here instead of hardcoding relative paths."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
GROUND_TRUTH_DIR = DATA_DIR / "ground_truth"
RAW_IMAGES_DIR = DATA_DIR / "raw_images"
OUTPUTS_DIR = DATA_DIR / "outputs"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
SCHEMAS_DIR = PROJECT_ROOT / "schemas"
RESULTS_DIR = PROJECT_ROOT / "results"
