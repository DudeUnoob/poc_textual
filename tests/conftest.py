import os
import sys
import tempfile
from pathlib import Path

# Make src importable in tests without installing the package.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
# Legacy unit tests mock Gemini; catalog-model integration tests pass @model
# explicitly. Never let a developer's local .env redirect mocked tests to a live API.
os.environ.setdefault("CENSUS_PROVIDER", "gemini")

# Configure an isolated database before any test module can import
# ``workbench.db``. Individual tests may override this path before their first
# import, but no import order can ever point the suite at the live workbench.
os.environ.setdefault("WORKBENCH_DATA_DIR", tempfile.mkdtemp(prefix="census-workbench-tests-"))
