import json
import tempfile
import unittest
from pathlib import Path

from herdr_layouts.catalog import preset_by_id
from herdr_layouts.herdr import HerdrError, MoveResult
from herdr_layouts.planner import snapshot_from_payload
from herdr_layouts.state import StateStore
from herdr_layouts.transaction import LayoutTransaction, RecoveryRequired, TransactionError


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def final_main_left_payload():
    return {
        "result": {
            "layout": {
                "workspace_id": "w1",
                "tab_id": "w1:t1",
                "focused_pane_id": "w1:p2",
                "zoomed": False,
                "area": {"x": 0, "y": 0, "width": 120, "height": 60},
                "panes": [
                    {"pane_id": "w1:p2", "rect": {"x": 0, "y": 0, "width": 72, "height": 60}},
                    {"pane_id": "w1:p1", "rect": {"x": 72, "y": 0, "width": 48, "height": 30}},
                    {"pane_id": "w1:p3", "rect": {"x": 72, "y": 30, "width": 48, "height": 30}},
                ],
                "splits": [],
            }
        }
    }


class FakeClient:
    def __init__(self, layouts, fail_insert_count=0, fail_all_inserts=False):
        self.layouts = list(layouts)
        self.fail_insert_count = fail_insert_count
        self.fail_all_inserts = fail_all_inserts
        self.calls = []

    def detach(self, pane_id):
        self.calls.append(("detach", pane_id))
        return MoveResult(True, pane_id, "w1:temp-" + pane_id.split(":")[-1])

    def insert(self, pane_id, tab_id, target_pane_id, direction, ratio):
        self.calls.append(("insert", pane_id, tab_id, target_pane_id, direction, ratio))
        if self.fail_all_inserts or self.fail_insert_count:
            if self.fail_insert_count:
                self.fail_insert_count -= 1
            raise HerdrError("injected insert refusal")
        return MoveResult(True, pane_id, tab_id, "w1:temp")

    def swap(self, source_pane_id, target_pane_id):
        self.calls.append(("swap", source_pane_id, target_pane_id))

    def layout(self, pane_id):
        self.calls.append(("layout", pane_id))
        if len(self.layouts) > 1:
            return self.layouts.pop(0)
        return self.layouts[0]


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp.name))
        self.original_payload = fixture("layout-3-main-left.json")
        self.snapshot = snapshot_from_payload(self.original_payload, "w1:p2")

    def tearDown(self):
        self.temp.cleanup()

    def test_apply_stages_all_nonanchors_then_reinserts_and_verifies(self):
        client = FakeClient([final_main_left_payload()])
        result = LayoutTransaction(client, self.store).apply(
            self.snapshot, preset_by_id("3-main-left")
        )
        self.assertTrue(result.verified)
        self.assertEqual(
            client.calls,
            [
                ("detach", "w1:p1"),
                ("detach", "w1:p3"),
                ("insert", "w1:p1", "w1:t1", "w1:p2", "right", 0.60),
                ("insert", "w1:p3", "w1:t1", "w1:p1", "down", 0.50),
                ("layout", "w1:p2"),
            ],
        )
        self.assertIsNone(self.store.read_journal())
        self.assertEqual(self.store.recent(3), ("3-main-left",))

    def test_insert_failure_rolls_back_and_clears_journal_after_verification(self):
        client = FakeClient(
            [self.original_payload, self.original_payload], fail_insert_count=1
        )
        with self.assertRaisesRegex(TransactionError, "rolled back"):
            LayoutTransaction(client, self.store).apply(
                self.snapshot, preset_by_id("3-main-left")
            )
        self.assertIsNone(self.store.read_journal())

    def test_rollback_failure_preserves_actionable_journal(self):
        client = FakeClient([self.original_payload], fail_all_inserts=True)
        with self.assertRaises(RecoveryRequired):
            LayoutTransaction(client, self.store).apply(
                self.snapshot, preset_by_id("3-main-left")
            )
        journal = self.store.read_journal()
        self.assertIsNotNone(journal)
        self.assertEqual(journal["source_tab_id"], "w1:t1")

    def test_verification_mismatch_triggers_verified_rollback(self):
        client = FakeClient(
            [self.original_payload, self.original_payload, self.original_payload]
        )
        with self.assertRaisesRegex(TransactionError, "rolled back"):
            LayoutTransaction(client, self.store).apply(
                self.snapshot, preset_by_id("3-main-left")
            )
        self.assertIsNone(self.store.read_journal())

    def test_recover_resumes_from_a_persisted_staging_checkpoint(self):
        self.store.write_journal(
            {
                "schema": 1,
                "source_tab_id": "w1:t1",
                "active_pane_id": "w1:p2",
                "source_payload": self.original_payload,
                "target_preset_id": "3-main-left",
                "phase": "staging",
                "completed": 1,
                "staged_panes": ["w1:p1"],
            }
        )
        client = FakeClient([self.original_payload, self.original_payload])
        result = LayoutTransaction(client, self.store).recover()
        self.assertTrue(result.verified)
        self.assertIsNone(self.store.read_journal())


if __name__ == "__main__":
    unittest.main()
