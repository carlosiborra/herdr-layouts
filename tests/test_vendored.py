import hashlib
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "herdr_layouts"

VENDORED = {
    "theme.py": "8415c9f04ed162db44bee68753954b58b3229a1a307b26b3f0100d51db482c24",
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
