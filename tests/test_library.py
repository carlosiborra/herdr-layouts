import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from herdr_layouts.catalog import preset_by_id
from herdr_layouts.library import Library, LibraryError, describe, tree_from_json, tree_to_json
from herdr_layouts.state import StateStore

MAIN_LEFT = {"split": "right", "ratio": 0.6, "first": 0,
             "second": {"split": "down", "ratio": 0.5, "first": 1, "second": 2}}


class TreeTests(unittest.TestCase):
    def test_round_trip_preserves_every_builtin(self):
        for preset_id in ("2-columns", "3-main-right", "4-focus-grid"):
            tree = preset_by_id(preset_id).tree
            self.assertEqual(tree_from_json(tree_to_json(tree)), tree)

    def test_rejects_malformed_trees(self):
        for bad in ({"split": "left", "first": 0, "second": 1},
                    {"split": "right", "ratio": 0.95, "first": 0, "second": 1},
                    {"split": "right", "ratio": True, "first": 0, "second": 1},
                    {"split": "right", "first": 0, "second": 7},
                    "0", None, True):
            with self.assertRaises(LibraryError, msg=repr(bad)):
                tree_from_json(bad)

    def test_custom_entries_need_leaves_zero_to_n(self):
        library = Library.load(None, [{"name": "Gap", "tree": {"split": "right", "first": 0, "second": 2}}])
        self.assertEqual(library.custom, [])
        self.assertIn("entry 1 skipped", library.problems[0])

    def test_describe_generates_text_for_custom_layouts(self):
        library = Library.load(None, [{"name": "Wide", "tree": MAIN_LEFT}])
        self.assertEqual(describe(library.custom[0]), "Your pane takes the left 60%, 2 other panes.")


class LibraryTests(unittest.TestCase):
    def test_saved_ids_are_unique_and_names_validated(self):
        library = Library()
        tree = tree_from_json(MAIN_LEFT)
        first, second = library.add_saved("Review", tree), library.add_saved("Review", tree)
        self.assertEqual((first.id, second.id), ("saved:review", "saved:review-2"))
        with self.assertRaises(LibraryError):
            library.add_saved("   ", tree)
        with self.assertRaises(LibraryError):
            library.add_saved("x" * 40, tree)

    def test_saved_layouts_keep_herdr_extreme_ratios(self):
        library = Library()
        saved = library.add_saved("Wide pane", tree_from_json(
            {"split": "right", "ratio": 0.9, "first": 0, "second": 1}))
        loaded = Library.load(json.loads(json.dumps(library.to_json())))
        self.assertEqual([preset.id for preset in loaded.saved], [saved.id])
        self.assertEqual(tree_to_json(loaded.saved[0].tree)["ratio"], 0.9)

    def test_favourites_survive_round_trip_and_drop_unknown_ids(self):
        library = Library()
        saved = library.add_saved("Review", tree_from_json(MAIN_LEFT))
        library.toggle_favorite(saved.id)
        library.toggle_favorite("3-columns")
        library.favorites.append("gone:id")
        loaded = Library.load(json.loads(json.dumps(library.to_json())))
        self.assertEqual(loaded.favorites, [saved.id, "3-columns"])
        self.assertEqual([preset.name for preset in loaded.favorite_presets()], ["Review", "Three Columns"])

    def test_delete_removes_favourite_and_only_targets_saved(self):
        library = Library()
        saved = library.add_saved("Review", tree_from_json(MAIN_LEFT))
        library.toggle_favorite(saved.id)
        library.delete_saved(saved.id)
        self.assertEqual((library.saved, library.favorites), ([], []))
        with self.assertRaises(LibraryError):
            library.delete_saved("3-columns")

    def test_duplicate_detection_matches_builtins_by_shape(self):
        library = Library()
        self.assertEqual(library.duplicate_of(tree_from_json(MAIN_LEFT)).id, "3-main-left")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StateStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_library_persists_and_recents_accept_saved_ids(self):
        library = self.store.load_library()
        saved = library.add_saved("Review", tree_from_json(MAIN_LEFT))
        self.store.save_library(library)
        fresh = StateStore(self.root)
        reloaded = fresh.load_library()
        self.assertEqual([preset.id for preset in reloaded.saved], [saved.id])
        fresh.record_recent(3, saved.id)
        self.assertEqual(fresh.recent(3)[0], saved.id)
        self.assertEqual(fresh.last(), saved.id)

    def test_custom_file_errors_are_reported_not_raised(self):
        (self.root / "layouts.json").write_text("{ not json")
        library = self.store.load_library()
        self.assertIn("not valid JSON", library.problems[0])
        (self.root / "layouts.json").write_text(json.dumps({"layouts": [{"name": "Wide", "tree": MAIN_LEFT}]}))
        library = self.store.load_library()
        self.assertEqual([preset.id for preset in library.custom], ["custom:wide"])

    def test_old_lock_files_are_pruned_and_fresh_ones_kept(self):
        old, fresh = self.root / "tab-old.lock", self.root / "tab-new.lock"
        old.write_text("")
        fresh.write_text("")
        past = time.time() - 3 * 86400
        os.utime(old, (past, past))
        self.assertEqual(self.store.prune_locks(), 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())


if __name__ == "__main__":
    unittest.main()
