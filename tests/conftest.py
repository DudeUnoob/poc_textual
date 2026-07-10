import sys
from pathlib import Path

# Make src importable in tests without installing the package.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
