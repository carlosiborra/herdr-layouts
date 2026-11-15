import json
import unittest
from pathlib import Path

from herdr_layouts.catalog import preset_by_id
from herdr_layouts.planner import (
    Move,
    PlanningError,
    Swap,
    expected_rectangles,
    topology_matches,
    plan_operations,
    snapshot_from_payload,
)


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


class PlannerTests(unittest.TestCase):
    def test_active_pane_is_anchor_and_others_keep_reading_order(self):
        snapshot = snapshot_from_payload(load_fixture("layout-3-main-left.json"), "w1:p2")
        self.assertEqual(
            plan_operations(snapshot, preset_by_id("3-main-left")),
            (
                Move("w1:p1", "w1:p2", "right", 0.60),
                Move("w1:p3", "w1:p1", "down", 0.50),
            ),
        )

    def test_anchor_can_be_placed_on_right_with_a_swap(self):
        snapshot = snapshot_from_payload(load_fixture("layout-3-main-left.json"), "w1:p2")
        self.assertEqual(
            plan_operations(snapshot, preset_by_id("3-main-right")),
            (
                Move("w1:p1", "w1:p2", "right", 0.40),
                Swap("w1:p2", "w1:p1"),
                Move("w1:p3", "w1:p1", "down", 0.50),
            ),
        )

    def test_mismatched_pane_count_is_rejected_before_planning(self):
        snapshot = snapshot_from_payload(load_fixture("layout-2-rows.json"), "w1:p1")
        with self.assertRaisesRegex(PlanningError, "requires 3 panes"):
            plan_operations(snapshot, preset_by_id("3-main-left"))

    def test_expected_grid_rectangles_are_normalized(self):
        self.assertEqual(
            expected_rectangles(preset_by_id("4-grid")),
            {
                0: (0.0, 0.0, 0.5, 0.5),
                1: (0.0, 0.5, 0.5, 0.5),
                2: (0.5, 0.0, 0.5, 0.5),
                3: (0.5, 0.5, 0.5, 0.5),
            },
        )

    def test_main_left_gives_the_anchor_sixty_percent_width(self):
        self.assertEqual(
            expected_rectangles(preset_by_id("3-main-left"))[0],
            (0.0, 0.0, 0.6, 1.0),
        )

    def test_zoomed_or_malformed_layout_is_rejected(self):
        payload = load_fixture("layout-2-rows.json")
        payload["result"]["layout"]["zoomed"] = True
        with self.assertRaisesRegex(PlanningError, "zoomed"):
            snapshot_from_payload(payload, "w1:p1")

    def test_verification_uses_pre_mutation_logical_pane_mapping(self):
        final_grid = snapshot_from_payload(load_fixture("layout-4-grid.json"), "w1:p1")
        self.assertTrue(
            topology_matches(
                final_grid,
                preset_by_id("4-grid"),
                pane_ids=("w1:p1", "w1:p2", "w1:p3", "w1:p4"),
            )
        )


if __name__ == "__main__":
    unittest.main()
