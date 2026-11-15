# HerdR Layouts

Visual, recoverable pane layouts for [HerdR](https://herdr.dev), the terminal workspace manager.

Pick a layout from a picker, press a key, and your panes rearrange without restarting anything.

```text
  Layouts                                                             2 panes   3 panes   4 panes
  this tab: 3 panes   ready

  ┏━━━━━━━━━┯━━━━━━━━┯━━━━━━━━━┓   ╭────────────────────────────╮   ╭────────────────┬───────────╮
  ┃         │        │         ┃   │           ● you            │   │                │           │
  ┃  ● you  │        │         ┃   ├────────────────────────────┤   │     ● you      ├───────────┤
  ┗━━━━━━━━━┷━━━━━━━━┷━━━━━━━━━┛   ╰────────────────────────────╯   ╰────────────────┴───────────╯
         1  Three Columns                   2  Three Rows                    3  Main Left

   enter  apply    1-9  pick    tab  group    f  favorite    a  auto    s  save tab    esc  close
```

*Abridged synthetic render of the picker (plain palette). The real picker is themed and fills the popup.*

## What it improves on raw `layout.apply`

A plain layout apply gives you no preview, no way back and no memory. HerdR Layouts shows every layout as a diagram, builds it from the panes you already have, verifies the result, and rolls back to the previous arrangement if anything fails. It also remembers favorites and saved layouts, and reaches layouts by easing split ratios when it can, so running processes are never moved.

HerdR Layouts arranges one to six panes into 32 built-in layouts, your own saved layouts, and layouts you write by hand, without closing existing pane processes. From a single pane it splits straight into the chosen layout; with two to four panes it rearranges them in place. Favorites open first and apply with one key. The picker follows your HerdR theme, labels the panes you already have, and marks the layout the tab already uses.

## Requirements

- HerdR 0.9.5 or newer (the tested version; uses `layout.export` and `layout.set_split_ratio`)
- Python 3.9 or newer
- macOS or Linux

The plugin uses only Python's standard library. It performs no downloads, network access, browser access, credential access, project scanning, agent control, or pane-input injection.

## Installation

```sh
herdr plugin install carlosiborra/herdr-layouts
```

The plugin provides a popup pane (`picker`) and an `open` action that opens it. Bind the action in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+shift+space"
type = "plugin_action"
command = "carlosiborra.layouts.open"
description = "choose a pane layout"
```

Then run `herdr config check` and `herdr server reload-config`. `python3 bin/herdr-layouts --version` inside the plugin directory prints the installed version.

## Keys

| Key | Action |
| --- | --- |
| `Enter` | Apply the selected layout |
| `1` to `9` | Apply that layout in the current group immediately |
| `Tab` / `Shift-Tab` (`]` / `[`) | Next / previous group: Favorites, 2 to 6 panes, Saved |
| Arrows or `h` `j` `k` `l` | Move; `Home` / `End` jump to the first or last |
| `f` | Add or remove the selected layout from Favorites |
| `a` | Make the selected layout the auto layout for its pane count, and adopt its pattern |
| `s` | Save the current tab's arrangement under a name |
| `r` / `d` | Rename / delete the selected saved layout |
| `Esc` or `q` | Close without changes (or cancel a prompt) |

When an earlier change was interrupted, the picker asks first: `r` restores the previous layout.

## Favorites and saved layouts

- **Favorites.** Press `f` on any layout, built-in or yours. While at least one favorite fits the current tab, the picker opens on Favorites, so `1` to `9` apply your most-used layouts straight away.
- **Saving.** Arrange a tab of two to four panes the way you like (including dragged ratios), open the picker there, and press `s`. Saving a shape that already exists asks first. Saved layouts keep your pane as the anchor.
- **Saved group.** Shows saved layouts of every pane count, tagged `2p`, `3p` or `4p`. Each applies like a built-in: from one pane it builds directly, with fewer panes it adds them, and with more panes it explains instead of closing anything.

Both live in `library.json` in the plugin config directory (HerdR sets `HERDR_PLUGIN_CONFIG_DIR` for the plugin; when the plugin runs outside HerdR it falls back to `~/.config/herdr/plugins/config/herdr-layouts`).

### Hand-written layouts

Create `layouts.json` in the same directory. Each layout is a name and a split tree. A tree is a pane index (0 is your pane, then 1 to 3) or a split:

```json
{
  "layouts": [
    {"name": "Editor and logs", "tree": {"split": "down", "ratio": 0.75, "first": 0, "second": 1}},
    {"name": "Wide editor", "tree": {
      "split": "right", "ratio": 0.7, "first": 0,
      "second": {"split": "down", "ratio": 0.5, "first": 1, "second": 2}}}
  ]
}
```

- `split` is `right` (side by side) or `down` (stacked); `ratio` is the first part's share, from 0.1 to 0.9.
- Use indexes 0 to n-1 exactly once, for two to four panes.
- Custom layouts appear in their pane-count group. An invalid entry is skipped and the reason is shown in the picker; the others still load.

## Auto layouts and patterns

Press `a` on a layout to make it the auto layout for that pane count. If the layout belongs to a pattern (Columns, Rows, Main Left, Main Right, Main Top, Main Bottom, Grid, Focus Grid), the pattern is adopted too, so every other pane count without its own choice follows it: pick Main Left once and 3, 4, 5 and 6 panes all become Main Left. Counts a pattern cannot fill borrow the closest relative (Main Left with two panes is Columns). Auto layouts show a teal `⟳`.

Headless commands for key bindings (`bin/herdr-layouts <flag>` inside a pane):

| Flag | Effect |
| --- | --- |
| `--auto` | Settle the tab into its auto layout |
| `--cycle` | Step to the next layout for this pane count |
| `--balance` | Give every pane an equal share |
| `--plan-auto` | Print how the auto layout would be reached; changes nothing |

Layouts are reached by **easing split ratios** whenever the tab's existing split tree can draw the target, which moves and restarts nothing. Only when panes would have to change places does the move engine run. After the popup closes, the change is eased over `animation_ms` (default 220, `0` turns it off, set in `library.json`).

## Theme

Colors come from your HerdR `config.toml` (`$HERDR_CONFIG_PATH`, else `$XDG_CONFIG_HOME/herdr/config.toml`, else `~/.config/herdr/config.toml`): the built-in palette named in `[theme]` (or `dark_name` / `light_name` with `auto_switch`), then `[theme.custom]` and the matching `[theme.custom.dark]` or `[theme.custom.light]` overrides. Your pane uses `accent`, new panes `yellow`, existing panes `surface0` and `surface1`, and borders blend `overlay0` into the background. With `panel_bg = "reset"` the picker blends against `surface_dim`, which should be close to your terminal background.

`NO_COLOR` is respected; the selected layout keeps a heavy border so selection never relies on color alone. Set `HERDR_LAYOUTS_APPEARANCE=light` or `dark` to override appearance detection.

## Behavior

- **One pane.** Applying a layout splits your pane directly into it; `● you` marks your pane and `+ new` the new ones. If HerdR fails partway, the new panes are closed.
- **Two to four panes.** The matching group (or Favorites) opens with the current layout selected and labelled `current`; applying it again does nothing. Existing panes are labelled with their agent or shell and folder. A layout with more panes creates them first.
- **Fewer panes than the tab has.** The group shows as a preview and explains what to close; nothing is closed for you.
- **Zoomed pane.** Unzoomed before applying.
- **More than four panes.** The picker opens and explains the limit.
- **Errors** stay in the popup instead of closing it.

## Safety and recovery

HerdR refuses direct same-tab moves. The plugin snapshots the source topology, stages non-anchor panes in temporary tabs, reconstructs the selected split tree, verifies the result, and removes its recovery journal only after success.

If an operation fails, the plugin reconstructs and verifies the source layout. If recovery cannot be verified, it keeps the journal and blocks new changes until recovery is retried.

Stored state is preset IDs, auto choices and the pattern name, saved layout names and trees, favorites, and recovery topology metadata. Pane labels are read for display only and never stored; terminal titles are not read at all. Nothing stores pane output, commands, working directories, or process content.

## Troubleshooting

Run with `HERDR_LAYOUTS_DEBUG=1` to append timings, theme resolution and errors to `debug.log` in the plugin config directory. It records layout IDs and counts only, never pane content. Lock files for old tabs are removed after a day.

## Why the plugin uses the HerdR socket

The HerdR CLI has no generic API-call command. Two operations the plugin needs, `layout.export` (the split tree with pane IDs) and `layout.set_split_ratio` (change one split without touching any pane), are only reachable through the local Unix socket. The plugin connects to `HERDR_SOCKET_PATH`; `~/.config/herdr/herdr.sock` is used only when that variable is unset. Everything else goes through `$HERDR_BIN_PATH`.

## Privacy and trust

The plugin reads the pane layout (pane IDs, geometry, and for display labels the agent or shell name and folder) and moves or resizes panes. It never reads pane contents, injects input, or touches the network. It is not sandboxed: it runs as you, like any HerdR plugin, so review the source before installing. See [SECURITY.md](SECURITY.md).

## Development and tests

```sh
python3 -m unittest discover -s tests -t .
python3 -m compileall -q src bin
```

`tests/live_acceptance.py` drives a real HerdR server and **rearranges and creates panes**. Run it only against a throwaway session, never your working one:

```sh
herdr --session scratch   # in another terminal
PYTHONPATH=src python3 tests/live_acceptance.py --session scratch --active-pane <pane-id>
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Update and uninstall

Update with `herdr plugin update carlosiborra.layouts` (or reinstall). To uninstall, remove the keybinding and run `herdr plugin uninstall carlosiborra.layouts`. Delete the plugin config directory too if you no longer want saved layouts and favorites.

## License

MIT, see [LICENSE](LICENSE).
