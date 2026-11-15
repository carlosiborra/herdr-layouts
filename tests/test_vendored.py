import hashlib
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "herdr_layouts"

VENDORED = {
    "theme.py": "24bececb50ce273a9d348a12774a861577460fa37129c8e1ad73289399d1c035",
    "terminal.py": "35fcaf6dd4e8581622e01b9bca0e67c221848d130c11c87e5f17831d65c7741d",
}


class VendoredDriftTest(unittest.TestCase):
    def test_vendored_helpers_unchanged(self):
        for name, expected in VENDORED.items():
            actual = hashlib.sha256((SRC / name).read_bytes()).hexdigest()
            self.assertEqual(
                actual,
                expected,
                f"{name} is a vendored helper and changed. Vendored helpers must be "
                "changed deliberately: update the hash in tests/test_vendored.py and "
                "add a CHANGELOG entry.",
            )


if __name__ == "__main__":
    unittest.main()
