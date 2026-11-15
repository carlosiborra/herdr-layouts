import unittest

from herdr_layouts.catalog import (
    FAMILY_NAMES,
    MAX_PANES,
    PRESETS,
    family_preset,
    iter_leaf_indexes,
    iter_ratios,
    presets_for_count,
)
from herdr_layouts.planner import expected_rectangles


class CatalogTests(unittest.TestCase):
    def test_catalog_has_exact_supported_counts(self):
        self.assertEqual(
            {count: len(presets_for_count(count)) for count in range(2, MAX_PANES + 1)},
            {2: 2, 3: 6, 4: 10, 5: 7, 6: 7},
        )

    def test_every_preset_has_unique_id_valid_ratio_and_anchor(self):
        ids = [preset.id for preset in PRESETS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 32)
        for preset in PRESETS:
            leaves = tuple(iter_leaf_indexes(preset.tree))
            self.assertEqual(sorted(leaves), list(range(preset.pane_count)))
            self.assertIn(0, leaves)
            self.assertTrue(all(0.15 <= ratio <= 0.85 for ratio in iter_ratios(preset.tree)), preset.id)

    def test_even_splits_give_equal_shares(self):
        widths = [rect[2] for rect in expected_rectangles(presets_for_count(5)[6]).values()]
        self.assertEqual(presets_for_count(5)[6].id, "5-columns")
        self.assertTrue(max(widths) - min(widths) < 0.02, widths)

    def test_unsupported_pane_counts_have_no_presets(self):
        self.assertEqual(presets_for_count(1), ())
        self.assertEqual(presets_for_count(MAX_PANES + 1), ())

    def test_families_cover_every_count_with_fallbacks(self):
        for family in FAMILY_NAMES:
            for count in range(2, MAX_PANES + 1):
                preset = family_preset(family, count)
                self.assertIsNotNone(preset, (family, count))
                self.assertEqual(preset.pane_count, count)
        self.assertEqual(family_preset("main-left", 2).id, "2-columns")
        self.assertEqual(family_preset("main-left", 5).id, "5-main-left")
        self.assertEqual(family_preset("grid", 3).id, "3-main-left")
        self.assertEqual(family_preset("grid", 6).id, "6-grid")
        self.assertIsNone(family_preset("nonexistent", 3))


if __name__ == "__main__":
    unittest.main()
