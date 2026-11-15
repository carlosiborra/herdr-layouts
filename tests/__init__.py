import sys
from pathlib import Path

# Let `python3 -m unittest` run from the plugin directory without PYTHONPATH.
_ROOT = Path(__file__).resolve().parents[1]
for _path in (_ROOT / "src", _ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
