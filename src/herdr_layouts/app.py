import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from . import __version__
from .catalog import FAMILY_NAMES, MAX_PANES, presets_for_count
from .herdr import HerdrClient, HerdrError
from .library import Library, LibraryError
from .model import Preset
from .planner import (
    LayoutSnapshot,
    PlanningError,
    index_plan,
    matching_preset_id,
    pane_ids_by_logical_index,
    preset_from_snapshot,
    snapshot_from_payload,
    topology_matches,
)
from .rpc import Rpc, connect
from .settle import fit_preset, hand_off, play_main, unfold_steps
from .renderer import Group, View, availability, first_visible_row, grid_for, move, render
from .state import StateError, StateStore
from .terminal import Terminal, TerminalExit
from .theme import Palette, load_palette
from .transaction import LayoutTransaction, RecoveryRequired, TransactionError

FAILURES = (HerdrError, PlanningError, StateError, TransactionError, LibraryError)
PANE_GROUPS = tuple(str(count) for count in range(2, MAX_PANES + 1))
SAVED_HINT = "No saved layouts yet.\nArrange a tab the way you like, open Layouts there, and press s."
FAVORITES_HINT = "No favorites yet.\nPress f on any layout to pin it here; 1-9 then apply it instantly."
NAME_CHARACTERS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_.()&+")


class AppError(RuntimeError):
    pass


def terminal_is_interactive(stdin, stdout) -> bool:
    return bool(stdin.isatty() and stdout.isatty())


class Log:
    """Opt-in diagnostics (HERDR_LAYOUTS_DEBUG=1): timings and errors, never pane content."""

    def __init__(self, path: Optional[Path]) -> None:
        self.path = path

    def write(self, message: str) -> None:
        if self.path is None:
            return
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), message))
        except OSError:
            self.path = None


class LayoutApp:
    def __init__(self, client: HerdrClient, store: StateStore, transaction: LayoutTransaction,
                 active_pane_id: str, terminal, palette: Optional[Palette] = None,
                 log: Optional[Log] = None, rpc: Optional[Rpc] = None) -> None:
        self.client = client
        self.store = store
        self.transaction = transaction
        self.active_pane_id = active_pane_id
        self.terminal = terminal
        self.palette = palette or Palette()
        self.log = log or Log(None)
        self.rpc = rpc
        # Animation to play after the popup closes: (tab_id, steps).
        self.handoff: Optional[Tuple[str, tuple]] = None
        self.library = Library()
        self._snapshot: Optional[LayoutSnapshot] = None
        self.current_id: Optional[str] = None
        self.pane_labels: Tuple[str, ...] = ()
        self.group_key = "2"
        self.selected = 0
        self.scroll_row = 0
        self.message = ""
        self.message_kind = "info"
        # Pending interaction: ("name", action, preset_id) while typing, ("confirm", action, preset_id).
        self.pending: Optional[Tuple[str, str, Optional[str]]] = None
        self.text = ""

    # State ---------------------------------------------------------------

    @property
    def snapshot(self) -> LayoutSnapshot:
        assert self._snapshot is not None, "layout not loaded"
        return self._snapshot

    @property
    def pane_count(self) -> int:
        return len(self.snapshot.panes)

    def _refresh(self) -> None:
        started = time.monotonic()
        self._snapshot = snapshot_from_payload(
            self.client.layout(self.active_pane_id), self.active_pane_id, strict=False
        )
        count = self.pane_count
        self.current_id = None
        if 2 <= count <= MAX_PANES and not self.snapshot.zoomed:
            candidates = presets_for_count(count) + tuple(
                preset for preset in self.library.user_layouts() if preset.pane_count == count)
            self.current_id = matching_preset_id(self.snapshot, candidates)
        try:
            labels = self.client.pane_labels(self.snapshot.tab_id)
            ordered = pane_ids_by_logical_index(self.snapshot) if count else ()
            self.pane_labels = tuple(labels.get(pane_id, "") for pane_id in ordered)
        except (HerdrError, AttributeError):
            self.pane_labels = ()
        self.log.write("refresh panes=%d current=%s ms=%d" % (
            count, self.current_id, (time.monotonic() - started) * 1000))

    def _groups(self) -> Tuple[Group, ...]:
        groups: List[Group] = []
        favorites = self.library.favorite_presets()
        if favorites:
            groups.append(Group("favorites", "★ Favorites", favorites, FAVORITES_HINT))
        for key in PANE_GROUPS:
            presets = presets_for_count(int(key)) + tuple(
                preset for preset in self.library.custom if preset.pane_count == int(key))
            groups.append(Group(key, "%s panes" % key, presets))
        groups.append(Group("saved", "Saved", tuple(self.library.saved), SAVED_HINT))
        return tuple(groups)

    @property
    def group(self) -> Group:
        groups = self._groups()
        return next((group for group in groups if group.key == self.group_key), groups[0])

    @property
    def presets(self) -> Tuple[Preset, ...]:
        return self.group.presets

    def _selected_preset(self) -> Optional[Preset]:
        presets = self.presets
        return presets[min(self.selected, len(presets) - 1)] if presets else None

    def _select_group(self, key: str, prefer: Optional[str] = None) -> None:
        keys = [group.key for group in self._groups()]
        self.group_key = key if key in keys else keys[0]
        ids = [preset.id for preset in self.presets]
        target = next((value for value in (prefer, self.current_id) if value in ids), None)
        self.selected = ids.index(target) if target else 0
        self.scroll_row = 0

    def _default_group(self) -> str:
        count = self.pane_count
        favorites = self.library.favorite_presets()
        if favorites and any(availability(preset.pane_count, count)[2] for preset in favorites):
            return "favorites"
        if 2 <= count <= MAX_PANES:
            return str(count)
        if count > MAX_PANES:
            return str(MAX_PANES)
        last = self.store.last()
        last_preset = self.library.find(last) if last else None
        return str(last_preset.pane_count) if last_preset and str(last_preset.pane_count) in PANE_GROUPS else "2"

    def _view(self) -> View:
        groups = self._groups()
        index = next((i for i, group in enumerate(groups) if group.key == self.group_key), 0)
        prompt, value = "", None
        if self.pending is not None:
            kind, action, preset_id = self.pending
            preset = self.library.find(preset_id) if preset_id else None
            if kind == "name":
                prompt = "Rename to:" if action == "rename" else "Save this tab as:"
                value = self.text
            elif action == "delete" and preset is not None:
                prompt = "Delete saved layout %s? y / n" % preset.name
            elif action == "overwrite" and preset is not None:
                prompt = "This tab already matches %s. Save a copy anyway? y / n" % preset.name
        return View(
            pane_count=self.pane_count, groups=groups, group_index=index, selected=self.selected,
            current_id=self.current_id, recent_id=self.store.last(),
            favorites=tuple(self.library.favorites),
            user_ids=tuple(preset.id for preset in self.library.user_layouts()),
            pane_labels=self.pane_labels, message=self.message, message_kind=self.message_kind,
            prompt=prompt, prompt_value=value, zoomed=self.snapshot.zoomed,
            auto_ids=tuple(preset.id for preset in (self.library.auto_for(count)
                                                     for count in range(2, MAX_PANES + 1)) if preset),
            pattern=FAMILY_NAMES.get(self.library.pattern, ""),
        )

    def _draw(self) -> None:
        width, height = self.terminal.size()
        if self.presets:
            grid = grid_for(width, height, self.presets)
            self.scroll_row = first_visible_row(self.selected, grid, self.scroll_row)
        lines = render(self._view(), width, height, self.palette, self.scroll_row)
        self.terminal.screen.draw(lines, (width, height))

    def _say(self, message: str, kind: str = "info") -> None:
        self.message, self.message_kind = message, kind

    def _persist(self) -> bool:
        try:
            self.store.save_library(self.library)
            return True
        except StateError as error:
            self._say("Could not save: %s" % error, "error")
            return False

    # Library actions -----------------------------------------------------

    def _toggle_favorite(self) -> None:
        preset = self._selected_preset()
        if preset is None:
            return
        added = self.library.toggle_favorite(preset.id)
        if not self._persist():
            self.library.toggle_favorite(preset.id)
            return
        self._say("%s %s favorites." % (preset.name, "added to" if added else "removed from"), "success")
        if self.group_key == "favorites":
            if not self.library.favorite_presets():
                self._select_group(self._default_group())
            else:
                self.selected = min(self.selected, len(self.presets) - 1)

    def _begin_save(self) -> None:
        if not 2 <= self.pane_count <= MAX_PANES or self.snapshot.zoomed:
            self._say("Save needs a tab with 2 to %d panes and no zoomed pane." % MAX_PANES, "info")
            return
        try:
            tree = preset_from_snapshot(self.snapshot).tree
        except PlanningError as error:
            self._say("This tab cannot be saved: %s" % error, "error")
            return
        duplicate = self.library.duplicate_of(tree)
        if duplicate is not None:
            self.pending = ("confirm", "overwrite", duplicate.id)
            return
        self.pending, self.text = ("name", "save", None), ""

    def _finish_name(self) -> None:
        assert self.pending is not None
        _, action, preset_id = self.pending
        self.pending = None
        name = self.text.strip()
        if not name:
            self._say("Cancelled: a name is required.", "info")
            return
        try:
            if action == "rename" and preset_id:
                previous = self.library.find(preset_id)
                preset = self.library.rename_saved(preset_id, name)
                if not self._persist():
                    if previous is not None:
                        self.library.rename_saved(preset_id, previous.name)
                    return
                self._say("Renamed to %s." % preset.name, "success")
                return
            tree = preset_from_snapshot(self.snapshot).tree
            preset = self.library.add_saved(name, tree)
        except (LibraryError, PlanningError) as error:
            self._say(str(error), "error")
            return
        if not self._persist():
            self.library.delete_saved(preset.id)
            return
        self.current_id = preset.id
        self._select_group("saved", prefer=preset.id)
        self._say("Saved %s. Press f to make it a favorite." % preset.name, "success")

    def _confirm(self) -> None:
        assert self.pending is not None
        _, action, preset_id = self.pending
        self.pending = None
        if action == "overwrite":
            self.pending, self.text = ("name", "save", None), ""
            return
        if action == "delete" and preset_id:
            before = self.library.to_json()
            try:
                preset = self.library.delete_saved(preset_id)
            except LibraryError as error:
                self._say(str(error), "error")
                return
            if not self._persist():
                restored = Library.load(before)
                self.library.saved, self.library.favorites = restored.saved, restored.favorites
                return
            self._say("Deleted %s." % preset.name, "success")
            if self.group_key == "favorites" and not self.library.favorite_presets():
                self._select_group(self._default_group())
            self.selected = max(0, min(self.selected, len(self.presets) - 1))

    def _handle_pending_key(self, key: str) -> None:
        assert self.pending is not None
        kind = self.pending[0]
        if key in ("escape", "interrupt"):
            self.pending = None
            self._say("Cancelled.", "info")
        elif kind == "confirm":
            if key in ("y", "Y", "enter"):
                self._confirm()
            elif key in ("n", "N"):
                self.pending = None
                self._say("Cancelled.", "info")
        elif key == "enter":
            self._finish_name()
        elif key == "backspace":
            self.text = self.text[:-1]
        elif len(key) == 1 and key in NAME_CHARACTERS and len(self.text) < 32:
            self.text += key

    # Applying ------------------------------------------------------------

    def _build_from_single_pane(self, preset: Preset) -> None:
        """Split the lone pane straight into the target tree; no staging tabs needed."""
        # The tab lock also tells effects plugins to keep out while panes are created.
        with self.store.tab_lock(self.snapshot.tab_id):
            self._split_into(preset)
        self.store.record_recent(preset.pane_count, preset.id)

    def _split_into(self, preset: Preset) -> None:
        pane_ids: Dict[int, str] = {0: self.active_pane_id}
        created: List[str] = []
        try:
            for kind, first, second, direction, ratio in index_plan(preset):
                if kind == "attach":
                    pane_ids[first] = self.client.split(pane_ids[second], direction=direction, ratio=ratio)
                    created.append(pane_ids[first])
                else:
                    self.client.swap(pane_ids[first], pane_ids[second])
            final = snapshot_from_payload(self.client.layout(self.active_pane_id), self.active_pane_id)
            ordered = tuple(pane_ids[index] for index in range(preset.pane_count))
            if not topology_matches(final, preset, pane_ids=ordered):
                raise TransactionError("the new panes did not match %s" % preset.name)
        except FAILURES as error:
            # The created panes are fresh shells, so closing them restores the original tab.
            for pane_id in reversed(created):
                try:
                    self.client.close(pane_id)
                except HerdrError:
                    pass
            raise TransactionError("%s; the new panes were closed" % error) from error

    def _apply(self, preset: Preset) -> bool:
        """Apply a layout. Returns True when the popup should close."""
        count = self.pane_count
        _, _, enabled = availability(preset.pane_count, count)
        if not enabled:
            if count > MAX_PANES:
                self._say("This tab has %d panes; layouts support up to %d." % (count, MAX_PANES), "info")
            else:
                self._say("%s needs %d panes and this tab has %d. Close panes yourself, then reopen."
                          % (preset.name, preset.pane_count, count), "info")
            return False
        if preset.id == self.current_id:
            return True
        self._say("Applying %s…" % preset.name, "busy")
        self._draw()
        started = time.monotonic()
        if self.snapshot.zoomed:
            self.client.unzoom(self.active_pane_id)
            self._refresh()
            count = self.pane_count
        if count == preset.pane_count and self._fit_by_ratios(preset):
            how = "ratios"
        elif count == 1:
            how = "build"
            self._build_from_single_pane(preset)
            self._queue_bloom()
        else:
            how = "moves"
            for _ in range(preset.pane_count - count):
                self.client.split(self.active_pane_id, direction="right", ratio=0.5)
            snapshot = snapshot_from_payload(self.client.layout(self.active_pane_id), self.active_pane_id)
            if len(snapshot.panes) != preset.pane_count:
                raise AppError("Herdr did not create the expected panes")
            self.transaction.apply(snapshot, preset)
            self._queue_bloom()
        self.log.write("applied %s from=%d via=%s ms=%d" % (
            preset.id, count, how, (time.monotonic() - started) * 1000))
        return True

    def _fit_by_ratios(self, preset: Preset) -> bool:
        """Reach the layout by easing split ratios alone; nothing moves or restarts."""
        if self.rpc is None:
            return False
        try:
            result = fit_preset(self.rpc, self.snapshot.tab_id, preset, self.active_pane_id)
        except HerdrError as error:
            self.log.write("ratio fit unavailable: %s" % error)
            return False
        if result is None:
            return False
        self.handoff = (self.snapshot.tab_id, result.steps)
        self.store.record_recent(preset.pane_count, preset.id)
        return True

    def _queue_bloom(self) -> None:
        """After a rebuild, grow the layout out of your pane once the popup is gone."""
        if self.rpc is None:
            return
        try:
            self.handoff = (self.snapshot.tab_id, tuple(unfold_steps(self.rpc, self.snapshot.tab_id,
                                                                     self.active_pane_id)))
        except HerdrError:
            self.handoff = None

    def _toggle_auto(self) -> None:
        preset = self._selected_preset()
        if preset is None:
            return
        before = self.library.to_json()
        on = self.library.toggle_auto(preset)
        if not self._persist():
            restored = Library.load(before)
            self.library.auto, self.library.pattern = restored.auto, restored.pattern
            return
        if on and preset.family:
            self._say("Auto: %s for %d panes; other counts follow the %s pattern." % (
                preset.name, preset.pane_count, FAMILY_NAMES[preset.family]), "success")
        elif on:
            self._say("Auto: %s whenever this tab has %d panes." % (preset.name, preset.pane_count), "success")
        else:
            self._say("%s is no longer the auto layout." % preset.name, "success")

    def _try_apply(self, preset: Preset) -> bool:
        try:
            return self._apply(preset)
        except RecoveryRequired as error:
            self.log.write("recovery required: %s" % error)
            self._say("Layout change interrupted: %s. Press r to recover." % error, "error")
        except (AppError,) + FAILURES as error:
            self.log.write("apply failed: %s" % error)
            self._say(str(error), "error")
        try:
            self._refresh()
        except FAILURES:
            pass
        return False

    # Recovery ------------------------------------------------------------

    def _recovery_prompt(self) -> bool:
        """Block until the user recovers or leaves. Returns True when the picker may continue."""
        paint = self.palette.paint
        while True:
            width, height = self.terminal.size()
            lines = [
                "", paint("  Layouts", "accent", bold=True), "",
                paint("  An earlier layout change did not finish.", "red", bold=True),
                paint("  Your panes are safe; some may be parked in a temporary tab.", "subtext0"), "",
                "  " + paint("r", "accent") + paint(" restore the previous layout   ", "subtext0")
                + paint("esc", "accent") + paint(" close", "subtext0"),
            ]
            if self.message:
                lines += ["", paint("  " + self.message, "red")]
            self.terminal.screen.draw(lines[:height], (width, height))
            for key in self.terminal.keys():
                if key in ("escape", "q", "interrupt"):
                    return False
                if key == "r":
                    try:
                        self.transaction.recover()
                        self._say("Previous layout restored.", "success")
                        return True
                    except FAILURES as error:
                        self._say(str(error), "error")

    # Loop ----------------------------------------------------------------

    def _startup_message(self) -> None:
        count = self.pane_count
        problems = self.library.problems
        if problems:
            extra = " (+%d more)" % (len(problems) - 1) if len(problems) > 1 else ""
            self._say(problems[0] + extra, "error")
        elif count > MAX_PANES:
            self._say("This tab has %d panes. Layouts support up to %d." % (count, MAX_PANES), "info")
        elif self.snapshot.zoomed:
            self._say("This pane is zoomed. Applying a layout unzooms it first.", "info")
        elif count == 1:
            self._say("Pick a layout to split this pane. Your pane stays where ● is.", "info")

    def _handle_key(self, key: str) -> Optional[int]:
        """Process one key; return an exit code to close the popup."""
        if self.pending is not None:
            self._handle_pending_key(key)
            return None
        if key in ("escape", "q", "interrupt"):
            return 0
        width, height = self.terminal.size()
        columns = grid_for(width, height, self.presets).columns if self.presets else 1
        if key in ("left", "right", "up", "down", "h", "j", "k", "l", "home", "end"):
            self.selected = move(self.selected, key, columns, len(self.presets))
            if self.message_kind != "error":
                self.message = ""
        elif key in ("tab", "shift_tab", "]", "["):
            keys = [group.key for group in self._groups()]
            step = 1 if key in ("tab", "]") else -1
            index = (keys.index(self.group_key) + step) % len(keys) if self.group_key in keys else 0
            self._select_group(keys[index], prefer=self.store.last())
            self.message = ""
        elif key in ("page_down", "page_up"):
            step = columns * 2 if key == "page_down" else -columns * 2
            self.selected = max(0, min(len(self.presets) - 1, self.selected + step)) if self.presets else 0
        elif key == "f":
            self._toggle_favorite()
        elif key == "a":
            self._toggle_auto()
        elif key == "s":
            self._begin_save()
        elif key == "r" and self.message_kind == "error" and self.store.read_journal() is not None:
            if not self._recovery_prompt():
                return 2
            self._refresh()
        elif key in ("r", "d"):
            preset = self._selected_preset()
            if preset is None or not preset.id.startswith("saved:"):
                self._say("Only saved layouts can be renamed or deleted.", "info")
            elif key == "r":
                self.pending, self.text = ("name", "rename", preset.id), preset.name
            else:
                self.pending = ("confirm", "delete", preset.id)
        elif key.isdigit() and key != "0":
            index = int(key) - 1
            if index < len(self.presets):
                self.selected = index
                if self._try_apply(self.presets[index]):
                    return 0
        elif key == "enter":
            preset = self._selected_preset()
            if preset is not None and self._try_apply(preset):
                return 0
        return None

    def run(self) -> int:
        if self.store.read_journal() is not None and not self._recovery_prompt():
            return 2
        self.library = self.store.load_library()
        self._refresh()
        self._select_group(self._default_group(), prefer=self.store.last())
        self._startup_message()
        while True:
            # Redrawing on every timeout tick picks up popup resizes; unchanged lines cost nothing.
            self._draw()
            for key in self.terminal.keys():
                code = self._handle_key(key)
                if code is not None:
                    return code


def _config_root(environ: Mapping[str, str]) -> Path:
    """Saved layouts and favorites live here: HERDR_PLUGIN_CONFIG_DIR when Herdr provides it.

    The home-relative fallback only applies when the plugin runs outside Herdr (for example from a shell).
    """
    configured = environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if configured:
        return Path(configured)
    return Path.home() / ".config" / "herdr" / "plugins" / "config" / "carlosiborra.layouts"


def _herdr_config_path(environ: Mapping[str, str]) -> Path:
    configured = environ.get("HERDR_CONFIG_PATH")
    if configured:
        return Path(configured)
    base = environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "herdr" / "config.toml"


def open_argv(environ: Mapping[str, str]) -> list:
    """The argv that asks Herdr to open the picker as this plugin's popup pane."""
    return [
        environ.get("HERDR_BIN_PATH") or "herdr", "plugin", "pane", "open",
        "--plugin", environ.get("HERDR_PLUGIN_ID") or "carlosiborra.layouts",
        "--entrypoint", "picker", "--focus",
    ]


def open_picker(environ: Mapping[str, str]) -> int:
    try:
        result = subprocess.run(open_argv(environ), env=dict(environ), capture_output=True, text=True,
                                timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        print("Herdr Layouts could not open its picker: %s" % exc, file=sys.stderr)
        return 1
    if result.returncode != 0:
        print("Herdr Layouts could not open its picker: %s" % (result.stderr.strip() or result.returncode),
              file=sys.stderr)
        return 1
    return 0


class Headless:
    """Keyboard-free commands for key bindings.

    --auto      settle the tab into its auto layout (ratios first, the safe move engine otherwise)
    --cycle     step to the next layout for this pane count, ratios only when possible
    --balance   give every pane an equal share by easing ratios
    --plan-auto print, as JSON, how the auto layout would be reached; changes nothing
    """

    def __init__(self, client: HerdrClient, store: StateStore, rpc: Optional[Rpc], active_pane_id: str,
                 entry: Path, environ: Mapping[str, str], log: Log) -> None:
        self.client, self.store, self.rpc = client, store, rpc
        self.active_pane_id, self.entry, self.environ, self.log = active_pane_id, entry, environ, log
        self.library = store.load_library()

    def _snapshot(self) -> LayoutSnapshot:
        return snapshot_from_payload(self.client.layout(self.active_pane_id), self.active_pane_id, strict=False)

    def _animate(self, tab_id: str, steps) -> None:
        if self.rpc is not None and steps:
            # Headless runs have no popup to wait for.
            hand_off(tab_id, steps, self.library.animation_ms, self.environ, self.entry, self.rpc, delay=0.0)

    def plan(self) -> Dict[str, object]:
        snapshot = self._snapshot()
        count = len(snapshot.panes)
        target = self.library.auto_for(count) if not snapshot.zoomed else None
        result: Dict[str, object] = {"tab_id": snapshot.tab_id, "panes": count,
                                     "target": target.id if target else None, "via": None, "steps": []}
        if target is None or self.rpc is None:
            return result
        if matching_preset_id(snapshot, (target,)) == target.id:
            result["via"] = "already"
            return result
        fitted = fit_preset(self.rpc, snapshot.tab_id, target, None)
        if fitted is not None:
            result["via"] = "ratios"
            result["main_pane"] = next((pane for pane, index in fitted.assignment.items() if index == 0), None)
            result["steps"] = [[list(step.path), step.start, step.end] for step in fitted.steps]
        else:
            result["via"] = "moves"
        return result

    def auto(self, allow_moves: bool = True, grow: Optional[str] = None) -> str:
        """Settle into the auto layout. Returns how: none, already, ratios or moves.

        ``grow`` names a just-created pane; its split starts collapsed so the
        pane slides open while everything eases into place.
        """
        snapshot = self._snapshot()
        count = len(snapshot.panes)
        target = self.library.auto_for(count)
        if target is None or snapshot.zoomed or count < 2:
            return "none"
        if matching_preset_id(snapshot, (target,)) == target.id:
            return "already"
        return self._reach(snapshot, target, allow_moves, prefer_active=allow_moves, grow=grow)

    def cycle(self) -> int:
        snapshot = self._snapshot()
        count = len(snapshot.panes)
        if snapshot.zoomed or not 2 <= count <= MAX_PANES:
            return 0
        options = presets_for_count(count) + tuple(p for p in self.library.user_layouts() if p.pane_count == count)
        current = matching_preset_id(snapshot, options)
        ids = [preset.id for preset in options]
        start = ids.index(current) + 1 if current in ids else 0
        # Prefer the next layout reachable by ratios alone; it is instant and moves nothing.
        ordered = options[start:] + options[:start]
        for preset in ordered:
            if self.rpc is not None and fit_preset(self.rpc, snapshot.tab_id, preset, self.active_pane_id):
                self._reach(snapshot, preset, allow_moves=False)
                return 0
        if ordered:
            self._reach(snapshot, ordered[0], allow_moves=True)
        return 0

    def balance(self) -> int:
        if self.rpc is None:
            return 1
        snapshot = self._snapshot()
        from .fit import balance
        steps = [step for step in balance(self.rpc.export(snapshot.tab_id)["root"]) if abs(step.end - step.start) > 0.005]
        self._animate(snapshot.tab_id, steps)
        return 0

    def _reach(self, snapshot: LayoutSnapshot, preset: Preset, allow_moves: bool,
               prefer_active: bool = True, grow: Optional[str] = None) -> str:
        if self.rpc is not None:
            # Automatic changes keep the biggest pane biggest; key presses put your pane first.
            fitted = fit_preset(self.rpc, snapshot.tab_id, preset, self.active_pane_id) if prefer_active else None
            if fitted is None:
                fitted = fit_preset(self.rpc, snapshot.tab_id, preset, None)
            if fitted is not None:
                steps = fitted.steps
                if grow:
                    from .fit import grow_from
                    steps = grow_from(steps, self.rpc.export(snapshot.tab_id)["root"], grow)
                self.store.record_recent(preset.pane_count, preset.id)
                self._animate(snapshot.tab_id, steps)
                self.log.write("headless %s via=ratios" % preset.id)
                return "ratios"
        if not allow_moves:
            return "none"
        LayoutTransaction(self.client, self.store).apply(snapshot, preset)
        if self.rpc is not None:
            self._animate(snapshot.tab_id, unfold_steps(self.rpc, snapshot.tab_id, self.active_pane_id))
        self.log.write("headless %s via=moves" % preset.id)
        return "moves"


def _context_pane_id(env: Mapping[str, str]) -> Optional[str]:
    """Popup panes receive the focused pane only in HERDR_PLUGIN_CONTEXT_JSON."""
    try:
        value = json.loads(env.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}").get("focused_pane_id")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None


def main(argv=None, environ: Optional[Mapping[str, str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ if environ is None else environ)
    if "--version" in args:
        print("herdr-layouts %s" % __version__)
        return 0
    if sys.version_info < (3, 9):
        print("Herdr Layouts requires Python 3.9 or newer", file=sys.stderr)
        return 2
    if args[:1] == ["--open"]:
        return open_picker(env)
    root = _config_root(env)
    entry = Path(sys.argv[0]).resolve()
    if args[:1] == ["--play"] and len(args) == 2:
        rpc = connect(env)
        return play_main(args[1], rpc, StateStore(root)) if rpc is not None else 1
    if env.get("HERDR_ENV") != "1":
        print("Herdr Layouts must run inside a Herdr pane", file=sys.stderr)
        return 2
    active_pane_id = env.get("HERDR_ACTIVE_PANE_ID") or env.get("HERDR_PANE_ID") or _context_pane_id(env)
    if not active_pane_id:
        print("Herdr did not provide an active pane ID", file=sys.stderr)
        return 2
    headless = next((arg for arg in args if arg in ("--auto", "--auto-ratios", "--cycle", "--balance", "--plan-auto")), None)
    if headless is not None:
        log = Log(root / "debug.log" if env.get("HERDR_LAYOUTS_DEBUG") == "1" else None)
        runner = Headless(HerdrClient(env.get("HERDR_BIN_PATH", "herdr")), StateStore(root), connect(env),
                          active_pane_id, entry, env, log)
        try:
            if headless == "--plan-auto":
                print(json.dumps(runner.plan()))
                return 0
            if headless in ("--auto", "--auto-ratios"):
                grow = args[args.index("--grow") + 1] if "--grow" in args[:-1] else None
                via = runner.auto(allow_moves=headless == "--auto", grow=grow)
                if "--json" in args:
                    print(json.dumps({"via": via}))
                return 0
            return runner.cycle() if headless == "--cycle" else runner.balance()
        except (AppError,) + FAILURES as exc:
            log.write("headless %s failed: %s" % (headless, exc))
            print("Herdr Layouts: %s" % exc, file=sys.stderr)
            return 1
    if not terminal_is_interactive(sys.stdin, sys.stdout):
        print(
            "Herdr Layouts must run in an interactive pane; use the open action or --open",
            file=sys.stderr,
        )
        return 2
    log = Log(root / "debug.log" if env.get("HERDR_LAYOUTS_DEBUG") == "1" else None)
    client = HerdrClient(env.get("HERDR_BIN_PATH", "herdr"))
    store = StateStore(root)
    store.prune_locks()
    transaction = LayoutTransaction(client, store)
    palette, theme = load_palette(_herdr_config_path(env), env)
    log.write("start version=%s python=%s theme=%s appearance=%s notes=%s" % (
        __version__, sys.version.split()[0], theme.name, theme.appearance, list(theme.notes)))
    rpc = connect(env)
    try:
        with Terminal(sys.stdin, sys.stdout) as terminal:
            app = LayoutApp(client, store, transaction, active_pane_id, terminal, palette, log, rpc)
            code = app.run()
        if app.handoff is not None and rpc is not None:
            tab_id, steps = app.handoff
            hand_off(tab_id, steps, app.library.animation_ms, env, entry, rpc)
        return code
    except (AppError,) + FAILURES as exc:
        log.write("fatal: %s" % exc)
        print("Herdr Layouts: %s" % exc, file=sys.stderr)
        return 1
    except TerminalExit:
        return 130
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
