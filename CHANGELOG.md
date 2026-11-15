# Changelog

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
