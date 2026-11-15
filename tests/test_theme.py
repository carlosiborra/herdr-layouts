import unittest

from herdr_layouts.theme import BUILTIN, Palette, TOKENS, parse_color, read_theme_tables, resolve_theme

USER = """
onboarding = false
[theme]
name = "catppuccin"  # comment
[theme.custom]
accent = "#e6b450"
panel_bg = "reset"
surface_dim = "#10141c"
text = 'rgb(191, 189, 182)'
bogus = "#000000"
[terminal]
accent = "#ffffff"
"""


class ThemeTests(unittest.TestCase):
    def test_every_builtin_defines_every_token(self):
        for name, palette in BUILTIN.items():
            self.assertEqual(set(palette), set(TOKENS), name)

    def test_custom_layer_overrides_and_reset_uses_surface_dim(self):
        theme = resolve_theme(USER, {})
        self.assertEqual(theme.colors["accent"], "#e6b450")
        self.assertEqual(theme.colors["text"], "#bfbdb6")
        self.assertEqual(theme.colors["panel_bg"], "#10141c")
        self.assertEqual(theme.colors["blue"], BUILTIN["catppuccin"]["blue"])
        self.assertEqual(theme.appearance, "dark")

    def test_only_theme_tables_are_read(self):
        tables = read_theme_tables(USER)
        self.assertNotIn("terminal", tables)
        self.assertEqual(tables["theme"]["name"], "catppuccin")

    def test_auto_switch_selects_light_theme_and_light_layer(self):
        source = ('[theme]\nauto_switch = true\ndark_name = "tokyo-night"\nlight_name = "catppuccin-latte"\n'
                  '[theme.custom.light]\ntext = "#101010"\n[theme.custom.dark]\ntext = "#fafafa"\n')
        light = resolve_theme(source, {}, appearance="light")
        dark = resolve_theme(source, {}, appearance="dark")
        self.assertEqual((light.name, light.colors["text"]), ("catppuccin-latte", "#101010"))
        self.assertEqual((dark.name, dark.colors["text"]), ("tokyo-night", "#fafafa"))

    def test_unknown_theme_and_bad_colours_are_noted(self):
        theme = resolve_theme('[theme]\nname = "neon"\n[theme.custom]\naccent = "nope"\n', {})
        self.assertEqual(theme.name, "catppuccin")
        self.assertEqual(len(theme.notes), 2)

    def test_colour_parsing(self):
        self.assertEqual(parse_color("#ABCDEF"), "#abcdef")
        self.assertEqual(parse_color("rgb(1,2,3)"), "#010203")
        self.assertIsNone(parse_color("rgb(300,0,0)"))
        self.assertEqual(parse_color("reset"), "reset")

    def test_roles_follow_the_accent(self):
        palette = Palette("truecolor", dict(BUILTIN["dracula"]))
        self.assertEqual(palette.role("border_selected"), BUILTIN["dracula"]["accent"])
        self.assertNotEqual(palette.role("you_fill"), palette.role("other_fill"))


if __name__ == "__main__":
    unittest.main()
