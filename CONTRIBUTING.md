# Contributing

Issues and pull requests are welcome.

## Setup

No dependencies: the plugin uses only the Python 3.9+ standard library.

```sh
python3 -m unittest discover -s tests -t .
python3 -m compileall -q src bin
```

CI runs the tests on Ubuntu and macOS with Python 3.9 and 3.13, and validates `herdr-plugin.toml`.

## Guidelines

- Add or update tests for every behavior change, including a failure case.
- `src/herdr_layouts/theme.py` and `src/herdr_layouts/terminal.py` are local copies vendored into this repository; edit them here and keep them dependency-free. `tests/test_vendored.py` pins their sha256; after a deliberate change, update the hash table in that test and add a CHANGELOG entry.
- Keep the manifest valid: required fields present, and action and pane ids unique.
- `tests/live_acceptance.py` rearranges real panes. Run it only against a throwaway session (`herdr --session <scratch>`), never your working one.
- Do not commit personal paths, names or session data.
