import tempfile
import unittest
from pathlib import Path

from herdr_layouts.app import LayoutApp, terminal_is_interactive
from herdr_layouts.renderer import strip_ansi
from herdr_layouts.state import StateStore
from herdr_layouts.theme import Palette
from herdr_layouts.transaction import ApplyResult, RecoveryRequired, RecoveryResult

from tests.fakes import FakeRpc, GeometryClient, ScriptedTerminal

SINGLE = {"w1:p1": (0, 0, 120, 60)}
MAIN_LEFT = {"w1:p1": (0, 0, 72, 60), "w1:p2": (72, 0, 48, 30), "w1:p3": (72, 30, 48, 30)}


class FakeTransaction:
    def __init__(self, error=None):
        self.applied = []
        self.recoveries = 0
        self.error = error

    def apply(self, snapshot, preset):
        if self.error:
            raise self.error
        self.applied.append((len(snapshot.panes), preset.id))
        return ApplyResult(preset.id, True)

    def recover(self):
        self.recoveries += 1
        return RecoveryResult(True)


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp.name))
        self.transaction = FakeTransaction()

    def tearDown(self):
        self.temp.cleanup()

    def run_app(self, panes, keys, rpc=False, **client_args):
        self.client = GeometryClient(panes, **client_args)
        self.terminal = ScriptedTerminal(keys)
        self.rpc = FakeRpc(self.client) if rpc else None
        self.app = LayoutApp(self.client, self.store, self.transaction, "w1:p1", self.terminal,
                             Palette("none"), rpc=self.rpc)
        return self.app.run()

    def last_frame(self):
        return strip_ansi(self.terminal.frames[-1])

    def splits(self):
        return [call for call in self.client.calls if call[0] in ("split", "swap", "close")]

    def test_single_pane_opens_with_guidance_instead_of_failing(self):
        self.assertEqual(self.run_app(SINGLE, ["escape"]), 0)
        frame = self.last_frame()
        self.assertIn("this tab: 1 pane", frame)
        self.assertIn("Pick a layout to split this pane", frame)
        self.assertIn("adds 1 pane", frame)

    def test_single_pane_builds_main_right_directly_with_correct_geometry(self):
        self.assertEqual(self.run_app(SINGLE, ["tab", "4"]), 0)
        self.assertEqual(self.transaction.applied, [])
        self.assertEqual(len(self.client.panes), 3)
        # Anchor ends on the right 60%, the two new panes stack on the left.
        self.assertEqual(self.client.panes["w1:p1"], (48, 0, 72, 60))
        self.assertEqual(self.store.last(), "3-main-right")
        self.assertNotIn("close", [call[0] for call in self.client.calls])

    def test_single_pane_build_closes_created_panes_when_herdr_fails(self):
        self.assertEqual(self.run_app(SINGLE, ["tab", "tab", "1"], fail_on_split=2), 0)
        self.assertEqual(set(self.client.panes), {"w1:p1"})
        self.assertIn("injected split failure", self.last_frame())
        self.assertIn("new panes were closed", self.last_frame())

    def test_matching_layout_is_marked_current_and_enter_is_a_noop(self):
        self.assertEqual(self.run_app(MAIN_LEFT, ["enter"]), 0)
        self.assertEqual(self.transaction.applied, [])
        self.assertEqual(self.splits(), [])

    def test_grid_built_from_one_pane_is_recognised_as_current(self):
        self.run_app(SINGLE, ["tab", "tab", "1"])
        self.terminal = ScriptedTerminal(["escape"])
        app = LayoutApp(self.client, self.store, self.transaction, "w1:p1", self.terminal, Palette("none"))
        app.run()
        self.assertIn("Grid current", self.last_frame())

    def test_current_layout_is_preselected_and_labelled(self):
        self.run_app(MAIN_LEFT, ["escape"])
        frame = self.last_frame()
        self.assertIn("Main Left current", frame)
        self.assertIn("already applied", frame)

    def test_number_key_applies_from_the_visible_group(self):
        self.assertEqual(self.run_app(MAIN_LEFT, ["1"]), 0)
        self.assertEqual(self.transaction.applied, [(3, "3-columns")])

    def test_tab_switches_group_and_extra_panes_are_created_first(self):
        self.assertEqual(self.run_app(MAIN_LEFT, ["tab", "enter"]), 0)
        self.assertEqual(self.transaction.applied, [(4, "4-grid")])

    def test_smaller_layout_explains_instead_of_closing_panes(self):
        self.assertEqual(self.run_app(MAIN_LEFT, ["shift_tab", "enter", "escape"]), 0)
        self.assertEqual(self.transaction.applied, [])
        self.assertEqual(self.splits(), [])
        self.assertIn("Close panes yourself", self.last_frame())

    def test_apply_failure_stays_open_with_inline_error(self):
        self.transaction.error = RecoveryRequired("rollback topology could not be verified")
        self.assertEqual(self.run_app(MAIN_LEFT, ["1", "escape"]), 0)
        self.assertIn("Press r to recover", self.last_frame())

    def test_zoomed_tab_is_unzoomed_before_applying(self):
        self.assertEqual(self.run_app(MAIN_LEFT, ["1"], zoomed=True), 0)
        self.assertIn(("unzoom", "w1:p1"), self.client.calls)
        self.assertEqual(self.transaction.applied, [(3, "3-columns")])

    def test_more_than_six_panes_is_explained(self):
        panes = {"w1:p%d" % i: (i * 17, 0, 17, 60) for i in range(1, 8)}
        self.run_app(panes, ["enter", "escape"])
        self.assertIn("layouts support up to 6", self.last_frame())
        self.assertEqual(self.transaction.applied, [])

    def test_unfinished_journal_blocks_until_recovered(self):
        self.store.write_journal({"schema": 1, "source_tab_id": "w1:t1"})
        self.assertEqual(self.run_app(MAIN_LEFT, ["q"]), 2)
        self.assertIn("did not finish", self.last_frame())

    def test_recovery_key_runs_recovery_then_opens_picker(self):
        self.store.write_journal({"schema": 1, "source_tab_id": "w1:t1"})
        self.assertEqual(self.run_app(MAIN_LEFT, ["r", "escape"]), 0)
        self.assertEqual(self.transaction.recoveries, 1)

    def test_last_used_group_is_default_for_a_single_pane(self):
        self.store.record_recent(3, "3-main-left")
        self.run_app(SINGLE, ["escape"])
        self.assertIn("Main Left last", self.last_frame())

    def test_f_favourites_and_next_launch_opens_on_favourites(self):
        self.run_app(MAIN_LEFT, ["left", "f", "escape"])
        self.assertEqual(self.store.load_library().favorites, ["3-rows"])
        self.run_app(MAIN_LEFT, ["escape"])
        frame = self.last_frame()
        self.assertIn("★ Favorites 1", frame)
        self.assertIn("★ Three Rows", frame)

    def test_favourite_number_key_applies_it(self):
        library = self.store.load_library()
        library.toggle_favorite("3-columns")
        self.store.save_library(library)
        self.assertEqual(self.run_app(MAIN_LEFT, ["1"]), 0)
        self.assertEqual(self.transaction.applied, [(3, "3-columns")])

    def test_save_names_the_tab_and_backspace_edits(self):
        tilted = {"w1:p1": (0, 0, 84, 60), "w1:p2": (84, 0, 36, 20), "w1:p3": (84, 20, 36, 40)}
        self.run_app(tilted, ["s", "type:Reviewx", "backspace", "enter", "escape"])
        saved = self.store.load_library().saved
        self.assertEqual([preset.name for preset in saved], ["Review"])
        self.assertEqual(saved[0].pane_count, 3)
        self.assertIn("Saved Review", self.last_frame())

    def test_saving_a_builtin_shape_asks_first(self):
        self.run_app(MAIN_LEFT, ["s", "n", "escape"])
        self.assertEqual(self.store.load_library().saved, [])
        self.run_app(MAIN_LEFT, ["s", "y", "type:Mine", "enter", "escape"])
        self.assertEqual([p.name for p in self.store.load_library().saved], ["Mine"])

    def test_escape_cancels_naming_without_closing(self):
        tilted = {"w1:p1": (0, 0, 84, 60), "w1:p2": (84, 0, 36, 20), "w1:p3": (84, 20, 36, 40)}
        self.assertEqual(self.run_app(tilted, ["s", "type:abc", "escape", "escape"]), 0)
        self.assertEqual(self.store.load_library().saved, [])

    def test_rename_and_delete_saved_layouts(self):
        tilted = {"w1:p1": (0, 0, 84, 60), "w1:p2": (84, 0, 36, 20), "w1:p3": (84, 20, 36, 40)}
        self.run_app(tilted, ["s", "type:Draft", "enter", "escape"])
        self.run_app(tilted, ["tab"] * 4 + ["r"] + ["backspace"] * 5 + ["type:Final", "enter", "escape"])
        self.assertEqual([p.name for p in self.store.load_library().saved], ["Final"])
        self.run_app(tilted, ["tab"] * 4 + ["d", "y", "escape"])
        self.assertEqual(self.store.load_library().saved, [])

    def test_applying_a_saved_layout_uses_the_engine(self):
        tilted = {"w1:p1": (0, 0, 84, 60), "w1:p2": (84, 0, 36, 20), "w1:p3": (84, 20, 36, 40)}
        self.run_app(tilted, ["s", "type:Tilt", "enter", "escape"])
        self.assertEqual(self.run_app(MAIN_LEFT, ["tab"] * 4 + ["enter"]), 0)
        self.assertEqual(self.transaction.applied, [(3, "saved:tilt")])

    def test_saved_layout_is_recognised_as_current(self):
        tilted = {"w1:p1": (0, 0, 84, 60), "w1:p2": (84, 0, 36, 20), "w1:p3": (84, 20, 36, 40)}
        self.run_app(tilted, ["s", "type:Tilt", "enter", "escape"])
        self.run_app(tilted, ["tab"] * 4 + ["escape"])
        self.assertIn("Tilt current", self.last_frame())

    def test_rename_on_builtin_explains(self):
        self.run_app(MAIN_LEFT, ["r", "escape"])
        self.assertIn("Only saved layouts", self.last_frame())

    def test_same_tree_layout_is_reached_by_ratios_and_animated_after_close(self):
        # Main Left at 60% -> Main Left Stack is impossible (3 panes), but Main Left at 50%
        # reaches 60% by easing one ratio.
        even = {"w1:p1": (0, 0, 60, 60), "w1:p2": (60, 0, 60, 30), "w1:p3": (60, 30, 60, 30)}
        self.assertEqual(self.run_app(even, ["right", "right", "enter"], rpc=True), 0)
        self.assertEqual(self.transaction.applied, [])
        tab_id, steps = self.app.handoff
        self.assertEqual(tab_id, "w1:t1")
        self.assertEqual([(step.path, step.end) for step in steps if step.path == ()], [((), 0.6)])
        self.assertEqual(self.store.last(), "3-main-left")

    def test_layout_needing_moves_uses_the_engine_then_blooms(self):
        self.assertEqual(self.run_app(MAIN_LEFT, ["1"], rpc=True), 0)
        self.assertEqual(self.transaction.applied, [(3, "3-columns")])
        self.assertIsNotNone(self.app.handoff)

    def test_a_marks_auto_and_adopts_the_pattern(self):
        self.run_app(MAIN_LEFT, ["right", "right", "a", "escape"])
        library = self.store.load_library()
        self.assertEqual(library.auto, {3: "3-main-left"})
        self.assertEqual(library.pattern, "main-left")
        frame = self.last_frame()
        self.assertIn("⟳ auto: Main Left", frame)
        self.assertIn("⟳ Main Left", frame)
        self.run_app(MAIN_LEFT, ["right", "right", "a", "escape"])
        self.assertEqual(self.store.load_library().auto, {})

    def test_five_and_six_pane_tabs_get_their_own_groups(self):
        panes = {"w1:p1": (0, 0, 72, 60)}
        panes.update({"w1:p%d" % i: (72, (i - 2) * 15, 48, 15) for i in range(2, 6)})
        self.run_app(panes, ["escape"])
        frame = self.last_frame()
        self.assertIn("this tab: 5 panes", frame)
        self.assertIn("Main Left current", frame)

    def test_existing_panes_are_labelled_in_matching_layouts(self):
        self.run_app(MAIN_LEFT, ["escape"])
        self.assertIn("claude · repo", self.last_frame())

    def test_bad_custom_file_is_reported_inline(self):
        self.store.root.mkdir(parents=True, exist_ok=True)
        (self.store.root / "layouts.json").write_text("[{]")
        self.assertEqual(self.run_app(MAIN_LEFT, ["escape"]), 0)
        self.assertIn("not valid JSON", self.last_frame())

    def test_noninteractive_plugin_action_is_rejected(self):
        class Stream:
            def __init__(self, interactive):
                self.interactive = interactive

            def isatty(self):
                return self.interactive

        self.assertFalse(terminal_is_interactive(Stream(False), Stream(True)))
        self.assertTrue(terminal_is_interactive(Stream(True), Stream(True)))


if __name__ == "__main__":
    unittest.main()


class OpenArgvTests(unittest.TestCase):
    def test_open_argv_uses_plugin_id_and_bin_path(self):
        from herdr_layouts.app import open_argv
        self.assertEqual(
            open_argv({"HERDR_BIN_PATH": "/opt/herdr", "HERDR_PLUGIN_ID": "custom-id"}),
            ["/opt/herdr", "plugin", "pane", "open", "--plugin", "custom-id", "--entrypoint", "picker", "--focus"],
        )

    def test_open_argv_falls_back_to_defaults(self):
        from herdr_layouts.app import open_argv
        argv = open_argv({})
        self.assertEqual(argv[0], "herdr")
        self.assertEqual(argv[argv.index("--plugin") + 1], "herdr-layouts")
        self.assertEqual(argv[argv.index("--entrypoint") + 1], "picker")

    def test_open_flag_runs_argv_without_needing_a_pane(self):
        from unittest import mock
        from herdr_layouts.app import main
        with mock.patch("herdr_layouts.app.subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            self.assertEqual(main(["--open"], {"HERDR_BIN_PATH": "h"}), 0)
        self.assertEqual(run.call_args[0][0][:4], ["h", "plugin", "pane", "open"])

    def test_open_failure_is_reported(self):
        from unittest import mock
        from herdr_layouts.app import main
        with mock.patch("herdr_layouts.app.subprocess.run", side_effect=OSError("missing")):
            self.assertEqual(main(["--open"], {}), 1)

    def test_config_path_honours_xdg(self):
        from herdr_layouts.app import _herdr_config_path
        self.assertEqual(_herdr_config_path({"XDG_CONFIG_HOME": "/x"}), Path("/x/herdr/config.toml"))
        self.assertEqual(_herdr_config_path({"HERDR_CONFIG_PATH": "/c.toml", "XDG_CONFIG_HOME": "/x"}), Path("/c.toml"))
