# Changelog

## 0.5.0

- Bind favorites to keys: new actions `favorite-1` to `favorite-9` apply the Nth favorite directly, with the same transactional apply as the picker (rolled back on failure). Slot order is the picker's Favorites order. Empty slots, layouts that do not fit the tab, interrupted changes and failures show a Herdr notification and exit non-zero.

## 0.4.2

- Naming: the product is spelled Herdr everywhere (never HerdR); the manifest name is now "Herdr Layouts". Vendored helpers: HerdR → Herdr spelling only.
- README rewritten: tagline, install and keybinding up front, demo, project page link, suite links; the text render moved into a collapsed section.
- README claims now match the code: ratio easing when the split tree already matches, otherwise panes are moved, never closed or restarted.
- README fixed the fallback config directory to `carlosiborra.layouts`.

## 0.4.1

- Fixed the picker popup exiting immediately: popup panes receive the focused pane only in `HERDR_PLUGIN_CONTEXT_JSON`, which is now used when `HERDR_ACTIVE_PANE_ID` and `HERDR_PANE_ID` are absent.
- Added an animated demo to the README, status badges, and GitHub issue templates.
- Added a drift guard test (`tests/test_vendored.py`) that pins the vendored `theme.py` and `terminal.py` helpers by hash.

## 0.4.0

- Plugin id is now `carlosiborra.layouts` (namespaced, so it cannot clash with other `herdr-layouts` plugins). Bind actions as `carlosiborra.layouts.<action>`.

- Standalone, installable release: `herdr plugin install carlosiborra/herdr-layouts`.
- Added a `picker` popup pane (72% x 80%); the `open` action now opens it, so a `plugin_action` key binding works without an absolute path.
- `min_herdr_version` is now 0.9.5, the tested version.
- `config.toml` lookup honors `$XDG_CONFIG_HOME`.
- Completed `pyproject.toml`; added MIT license, security policy, contributing guide and CI.

## 0.3.0

- Previous private release.
