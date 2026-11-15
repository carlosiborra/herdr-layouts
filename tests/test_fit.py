import unittest

from herdr_layouts.catalog import preset_by_id
from herdr_layouts.fit import Step, at, balance, fit, frames, play, rectangles, unfold
from herdr_layouts.planner import expected_rectangles


def pane(pane_id):
    return {"type": "pane", "pane_id": pane_id}


def split(direction, ratio, first, second):
    return {"type": "split", "direction": direction, "ratio": ratio, "first": first, "second": second}


# The shape Herdr builds when you split a pane right and then split the new one down.
MAIN_LEFT_TREE = split("right", 0.5, pane("a"), split("down", 0.5, pane("b"), pane("c")))
# Three columns built by splitting the first pane twice: right(right(a, b), c).
LEFT_NESTED_COLUMNS = split("right", 0.66, split("right", 0.5, pane("a"), pane("b")), pane("c"))


def apply(root, fitted):
    return rectangles(root, {step.path: step.end for step in fitted.steps})


class FitTests(unittest.TestCase):
    def assertRects(self, actual, expected):
        for pane_id, rect in expected.items():
            for got, want in zip(actual[pane_id], rect):
                self.assertAlmostEqual(got, want, delta=0.01, msg=(pane_id, actual[pane_id], rect))

    def test_ratios_reach_a_layout_with_the_same_tree(self):
        target = preset_by_id("3-main-left")
        fitted = fit(MAIN_LEFT_TREE, expected_rectangles(target), "a")
        self.assertIsNotNone(fitted)
        self.assertEqual(fitted.assignment["a"], 0)
        self.assertRects(apply(MAIN_LEFT_TREE, fitted), {"a": expected_rectangles(target)[0]})
        self.assertEqual([step.path for step in fitted.changes], [()])

    def test_a_differently_nested_tree_still_fits(self):
        """Three columns drawn by right(right(a, b), c) are reachable without moving panes."""
        target = preset_by_id("3-columns")
        fitted = fit(LEFT_NESTED_COLUMNS, expected_rectangles(target), "a")
        self.assertIsNotNone(fitted)
        widths = sorted(rect[2] for rect in apply(LEFT_NESTED_COLUMNS, fitted).values())
        self.assertTrue(max(widths) - min(widths) < 0.03, widths)

    def test_a_layout_needing_moves_is_refused(self):
        self.assertIsNone(fit(MAIN_LEFT_TREE, expected_rectangles(preset_by_id("3-columns")), "a"))
        self.assertIsNone(fit(MAIN_LEFT_TREE, expected_rectangles(preset_by_id("4-grid")), "a"))

    def test_main_pane_may_be_required_or_free(self):
        target = expected_rectangles(preset_by_id("3-main-right"))
        tree = split("right", 0.5, split("down", 0.5, pane("b"), pane("c")), pane("a"))
        self.assertIsNone(fit(tree, target, "b"))
        self.assertEqual(fit(tree, target, "a").assignment["a"], 0)
        self.assertEqual(fit(tree, target, None).assignment["a"], 0)

    def test_least_change_assignment_keeps_the_biggest_pane_biggest(self):
        tree = split("right", 0.7, pane("big"), split("down", 0.5, pane("b"), pane("c")))
        fitted = fit(tree, expected_rectangles(preset_by_id("3-main-left")), None)
        self.assertEqual(fitted.assignment["big"], 0)

    def test_six_panes_fit_in_reasonable_time(self):
        tree = split("right", 0.5, pane("a"), split("down", 0.2, pane("b"), split("down", 0.25, pane("c"), split(
            "down", 0.33, pane("d"), split("down", 0.5, pane("e"), pane("f"))))))
        fitted = fit(tree, expected_rectangles(preset_by_id("6-main-left")), "a")
        self.assertIsNotNone(fitted)

    def test_unfold_opens_every_split_from_the_pane(self):
        steps = {step.path: step for step in unfold(MAIN_LEFT_TREE, "c")}
        self.assertEqual(steps[()].start, 0.1)  # c is in the second child: start from the far side
        self.assertEqual(steps[(True,)].start, 0.1)
        steps = {step.path: step for step in unfold(MAIN_LEFT_TREE, "a")}
        self.assertEqual(steps[()].start, 0.9)
        self.assertEqual(steps[()].end, 0.5)

    def test_balance_shares_space_along_a_direction(self):
        tree = split("right", 0.8, pane("a"), split("right", 0.3, pane("b"), pane("c")))
        steps = {step.path: step.end for step in balance(tree)}
        self.assertAlmostEqual(steps[()], 1 / 3, places=3)
        self.assertAlmostEqual(steps[(True,)], 0.5, places=3)

    def test_frames_land_exactly_and_never_leave_the_clamp(self):
        steps = [Step((), 0.1, 0.9)]
        sequence = frames(steps, 6, "out-back")
        self.assertEqual(sequence[-1], [((), 0.9)])
        for frame in sequence:
            self.assertTrue(0.1 <= frame[0][1] <= 0.9)
        self.assertEqual(at(steps, 0.0), [((), 0.1)])

    def test_play_follows_wall_time_and_writes_parents_first(self):
        now = [0.0]
        written = []
        play(lambda path, ratio: written.append((path, ratio)),
             [Step((True,), 0.2, 0.5), Step((), 0.3, 0.6)], 0.1,
             clock=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + 0.03))
        self.assertEqual(written[0][0], ())
        self.assertEqual(written[-2:], [((), 0.6), ((True,), 0.5)])
        self.assertLess(len(written), 14)

    def test_zero_duration_jumps_to_the_end(self):
        written = []
        play(lambda path, ratio: written.append((path, ratio)), [Step((), 0.3, 0.6)], 0)
        self.assertEqual(written, [((), 0.6)])


if __name__ == "__main__":
    unittest.main()
