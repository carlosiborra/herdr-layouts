import unittest

from herdr_layouts.catalog import PRESETS, presets_for_count
from herdr_layouts.library import Library
from herdr_layouts.renderer import (
    DESCRIPTIONS, Group, View, availability, footer_keys, grid_for, move, render, strip_ansi,
)
from herdr_layouts.terminal import KeyDecoder, Screen
from herdr_layouts.theme import Palette

PLAIN = Palette("none")


def groups(extra=()):
    base = tuple(Group(str(n), "%d panes" % n, presets_for_count(n)) for n in (2, 3, 4))
    return base + tuple(extra)


def view(pane_count=3, group=3, **kwargs):
    all_groups = kwargs.pop("groups", groups())
    index = next(i for i, g in enumerate(all_groups) if g.key == str(group))
    return View(pane_count, all_groups, group_index=index, **kwargs)


def frame(v, width=110, height=34, palette=PLAIN):
    lines = render(v, width, height, palette)
    return lines, "\n".join(strip_ansi(line) for line in lines)


class RendererTests(unittest.TestCase):
    def test_frame_fills_exactly_the_viewport(self):
        for width, height in ((110, 34), (60, 18), (200, 60)):
            lines, _ = frame(view(3, 4), width, height)
            self.assertEqual(len(lines), height)
            self.assertTrue(all(len(strip_ansi(line)) == width for line in lines))

    def test_every_preset_fits_on_one_screen_at_the_default_popup_size(self):
        for group in (2, 3, 4):
            _, text = frame(view(3, group), 110, 34)
            for preset in presets_for_count(group):
                self.assertIn(preset.name, text, preset.id)
            self.assertNotIn("more", text)

    def test_two_layouts_sit_side_by_side_in_a_wide_popup(self):
        grid = grid_for(120, 52, presets_for_count(2))
        self.assertEqual(grid.columns, 2)

    def test_one_pane_shows_new_panes_and_the_anchor(self):
        _, text = frame(view(1, 3))
        self.assertIn("● you", text)
        self.assertIn("+ new", text)
        self.assertIn("adds 2 panes", text)

    def test_existing_panes_show_their_labels(self):
        _, text = frame(view(3, 3, pane_labels=("you", "claude · repo", "shell · tmp")), 140, 40)
        self.assertIn("claude · repo", text)
        self.assertIn("shell · tmp", text)

    def test_selected_tile_uses_heavy_borders_without_colour(self):
        _, text = frame(view(3, 3, selected=1))
        self.assertEqual(text.count("┏"), 1)

    def test_current_and_favourite_are_labelled(self):
        _, text = frame(view(3, 3, selected=2, current_id="3-main-left", favorites=("3-main-left",)))
        self.assertIn("★ Main Left current", text)
        self.assertIn("already applied", text)

    def test_unavailable_group_explains_itself(self):
        _, text = frame(view(3, 2))
        self.assertIn("close 1 pane first", text)

    def test_messages_and_prompts_replace_the_detail_line(self):
        _, text = frame(view(message="boom", message_kind="error"))
        self.assertIn("boom", text)
        _, text = frame(view(prompt="Save this tab as:", prompt_value="Review"))
        self.assertIn("Save this tab as:", text)
        self.assertIn("Review▏", text)
        self.assertIn("enter  save", text)

    def test_empty_group_shows_its_hint(self):
        empty = Group("saved", "Saved", (), "No saved layouts yet.\nPress s.")
        _, text = frame(view(3, "saved", groups=groups((empty,))))
        self.assertIn("No saved layouts yet.", text)
        self.assertIn("Saved 0", text)

    def test_saved_layouts_show_pane_count_and_management_keys(self):
        library = Library()
        saved = library.add_saved("Review", presets_for_count(3)[2].tree)
        group = Group("saved", "Saved", (saved,))
        v = view(3, "saved", groups=groups((group,)), user_ids=(saved.id,))
        _, text = frame(v)
        self.assertIn("Review 3p", text)
        self.assertIn(("r", "rename"), footer_keys(v))

    def test_small_popup_shows_size_hint(self):
        _, text = frame(view(), 40, 10)
        self.assertIn("Enlarge the popup", text)

    def test_cramped_popup_scrolls_with_an_indicator(self):
        _, text = frame(view(3, 4, selected=9), 60, 18)
        self.assertIn("Focus Grid", text)
        self.assertIn("more", text)

    def test_stacked_presets_keep_your_pane_marked(self):
        _, text = frame(view(3, 4, selected=2), 110, 40)
        self.assertEqual(text.count("●"), 10)

    def test_availability_covers_each_pane_count_gap(self):
        self.assertEqual(availability(3, 3)[0], "ready")
        self.assertEqual(availability(4, 1)[0], "adds 3 panes")
        self.assertFalse(availability(2, 3)[2])
        self.assertFalse(availability(4, 5)[2])

    def test_movement_clamps_at_edges(self):
        self.assertEqual(move(0, "left", 3, 6), 0)
        self.assertEqual(move(2, "right", 3, 6), 2)
        self.assertEqual(move(1, "down", 3, 6), 4)
        self.assertEqual(move(4, "down", 3, 6), 4)
        self.assertEqual(move(4, "up", 3, 6), 1)
        self.assertEqual(move(3, "end", 3, 6), 5)

    def test_grid_fits_every_tile_and_grows_with_the_popup(self):
        four = presets_for_count(4)
        small, large = grid_for(110, 34, four), grid_for(200, 60, four)
        for grid in (small, large):
            self.assertEqual(grid.visible_rows, grid.total_rows)
        self.assertGreater(large.tile_width * large.tile_height, small.tile_width * small.tile_height)

    def test_full_catalog_ids_have_descriptions(self):
        self.assertEqual(set(DESCRIPTIONS), {preset.id for preset in PRESETS})

    def test_light_theme_frames_render(self):
        light = Palette("truecolor", {"panel_bg": "#eff1f5", "text": "#4c4f69", "accent": "#ea76cb",
                                      "surface0": "#ccd0da", "surface1": "#bcc0cc", "overlay0": "#9ca0b0",
                                      "yellow": "#df8e1d"}, "light")
        lines, _ = frame(view(1, 3), palette=light)
        self.assertTrue(any("\x1b[48;2;" in line for line in lines))


class TerminalTests(unittest.TestCase):
    def test_decoder_handles_split_sequences_and_controls(self):
        decoder = KeyDecoder()
        self.assertEqual(decoder.feed(b"\x1b["), ())
        self.assertEqual(decoder.feed(b"A\t\x1b[Z1\r"), ("up", "tab", "shift_tab", "1", "enter"))
        self.assertEqual(decoder.feed(b"\x7f"), ("backspace",))
        self.assertEqual(decoder.feed(b"\x1b"), ())
        self.assertEqual(decoder.flush_escape(), ("escape",))

    def test_screen_rewrites_only_changed_lines(self):
        writes = []
        screen = Screen(writes.append)
        screen.draw(["a", "b", "c"], (10, 3))
        self.assertIn("\x1b[2J", writes[0])
        writes.clear()
        screen.draw(["a", "B", "c"], (10, 3))
        self.assertEqual(len(writes), 1)
        self.assertIn("\x1b[2;1HB", writes[0])
        self.assertNotIn("\x1b[1;1H", writes[0])
        writes.clear()
        screen.draw(["a", "B", "c"], (10, 3))
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
