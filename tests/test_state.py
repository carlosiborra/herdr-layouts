import json
import tempfile
import unittest
from pathlib import Path

from herdr_layouts.state import StateStore, TabLockedError


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_recents_are_deduplicated_scoped_and_capped(self):
        for preset_id in ("3-columns", "3-main-left", "3-main-top", "3-rows", "3-main-left", "3-main-bottom"):
            self.store.record_recent(3, preset_id)
        self.assertEqual(
            self.store.recent(3),
            ("3-main-bottom", "3-main-left", "3-rows"),
        )
        self.assertEqual(self.store.recent(4), ())

    def test_unknown_or_corrupt_state_degrades_to_empty(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "state.json").write_text("not-json")
        self.assertEqual(self.store.recent(3), ())
        (self.root / "state.json").write_text(json.dumps({"recents": {"3": ["unknown"]}}))
        self.assertEqual(self.store.recent(3), ())

    def test_atomic_write_leaves_no_temporary_file(self):
        self.store.record_recent(2, "2-columns")
        self.assertTrue((self.root / "state.json").is_file())
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_journal_round_trip_and_clear(self):
        journal = {"schema": 1, "tab_id": "w1:t1", "completed": 2, "staged_panes": ["w1:p2"]}
        self.store.write_journal(journal)
        self.assertEqual(self.store.read_journal(), journal)
        self.store.clear_journal()
        self.assertIsNone(self.store.read_journal())

    def test_tab_lock_rejects_concurrent_owner(self):
        with self.store.tab_lock("w1:t1"):
            with self.assertRaises(TabLockedError):
                with self.store.tab_lock("w1:t1"):
                    pass


if __name__ == "__main__":
    unittest.main()
