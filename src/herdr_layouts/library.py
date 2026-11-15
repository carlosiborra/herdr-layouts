"""Saved, hand-written and favourite layouts alongside the built-in catalog.

Everything here is plain JSON in the plugin config directory:

- library.json   written by the picker: saved layouts and favourite IDs
- layouts.json   optional, written by hand: custom layouts in the same tree format

A layout tree is either a leaf index (0 is your pane) or
{"split": "right" | "down", "ratio": 0.1..0.9, "first": <tree>, "second": <tree>}.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

from .catalog import FAMILY_NAMES, MAX_PANES, PRESETS, family_preset, iter_leaf_indexes
from .model import Leaf, Node, Preset, Split

MIN_RATIO, MAX_RATIO = 0.1, 0.9
MAX_NAME = 32
SAVED_PREFIX = "saved:"
CUSTOM_PREFIX = "custom:"


class LibraryError(ValueError):
    pass


def tree_to_json(node: Node) -> object:
    if isinstance(node, Leaf):
        return node.index
    return {"split": node.direction, "ratio": round(node.ratio, 4),
            "first": tree_to_json(node.first), "second": tree_to_json(node.second)}


def tree_from_json(value: object, depth: int = 0) -> Node:
    if depth > 8:
        raise LibraryError("layout tree is nested too deeply")
    if isinstance(value, bool):
        raise LibraryError("leaf must be a pane index")
    if isinstance(value, int):
        if not 0 <= value < MAX_PANES:
            raise LibraryError("pane index %d is outside 0-%d" % (value, MAX_PANES - 1))
        return Leaf(value)
    if not isinstance(value, dict):
        raise LibraryError("each node must be a pane index or a split object")
    direction = value.get("split")
    if direction not in ("right", "down"):
        raise LibraryError('split must be "right" or "down"')
    ratio = value.get("ratio", 0.5)
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
        raise LibraryError("ratio must be a number")
    if not MIN_RATIO <= float(ratio) <= MAX_RATIO:
        raise LibraryError("ratio must be between %.2f and %.2f" % (MIN_RATIO, MAX_RATIO))
    return Split(direction, float(ratio), tree_from_json(value.get("first"), depth + 1),
                 tree_from_json(value.get("second"), depth + 1))


def validate_tree(tree: Node) -> int:
    """Return the pane count, requiring leaves 0..n-1 exactly once."""
    leaves = sorted(iter_leaf_indexes(tree))
    if leaves != list(range(len(leaves))) or not 2 <= len(leaves) <= MAX_PANES:
        raise LibraryError("leaves must be 0 to n-1 exactly once, with 2-%d panes" % MAX_PANES)
    return len(leaves)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "layout"


def clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise LibraryError("name cannot be empty")
    if len(name) > MAX_NAME:
        raise LibraryError("name must be %d characters or fewer" % MAX_NAME)
    return name


def preset_from_entry(entry: Mapping[str, object], prefix: str) -> Preset:
    if not isinstance(entry, dict):
        raise LibraryError("each layout must be an object")
    name = entry.get("name")
    if not isinstance(name, str):
        raise LibraryError("layout is missing a name")
    name = clean_name(name)
    tree = tree_from_json(entry.get("tree"))
    count = validate_tree(tree)
    identifier = entry.get("id")
    identifier = identifier if isinstance(identifier, str) and identifier.startswith(prefix) else prefix + slug(name)
    return Preset(identifier, name, count, tree)


def same_shape(first: Node, second: Node, tolerance: float = 0.02) -> bool:
    if isinstance(first, Leaf) or isinstance(second, Leaf):
        return isinstance(first, Leaf) and isinstance(second, Leaf) and first.index == second.index
    return (first.direction == second.direction and abs(first.ratio - second.ratio) <= tolerance
            and same_shape(first.first, second.first, tolerance)
            and same_shape(first.second, second.second, tolerance))


@dataclass
class Library:
    saved: List[Preset] = field(default_factory=list)
    custom: List[Preset] = field(default_factory=list)
    favorites: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    # Auto layouts: an explicit choice per pane count, and a pattern family that
    # fills in the counts without one, so "main left" follows panes as they come and go.
    auto: Dict[int, str] = field(default_factory=dict)
    pattern: str = ""
    # Length of layout animations; 0 turns them off.
    animation_ms: int = 220

    def all(self) -> Tuple[Preset, ...]:
        return tuple(PRESETS) + tuple(self.custom) + tuple(self.saved)

    def find(self, preset_id: str) -> Optional[Preset]:
        return next((preset for preset in self.all() if preset.id == preset_id), None)

    def user_layouts(self) -> Tuple[Preset, ...]:
        return tuple(self.saved) + tuple(self.custom)

    def favorite_presets(self) -> Tuple[Preset, ...]:
        found = (self.find(preset_id) for preset_id in self.favorites)
        return tuple(preset for preset in found if preset is not None)

    def is_favorite(self, preset_id: str) -> bool:
        return preset_id in self.favorites

    def toggle_favorite(self, preset_id: str) -> bool:
        if preset_id in self.favorites:
            self.favorites.remove(preset_id)
            return False
        self.favorites.append(preset_id)
        return True

    def auto_for(self, count: int) -> Optional[Preset]:
        """The layout a tab with this many panes should settle into, if any."""
        chosen = self.find(self.auto.get(count, ""))
        if chosen is not None and chosen.pane_count == count:
            return chosen
        if self.pattern:
            return family_preset(self.pattern, count)
        return None

    def auto_source(self, count: int) -> str:
        """Why auto_for picked its layout: "chosen", "pattern" or ""."""
        if count in self.auto and self.find(self.auto[count]) is not None:
            return "chosen"
        return "pattern" if self.pattern and family_preset(self.pattern, count) else ""

    def toggle_auto(self, preset: Preset) -> bool:
        """Make a layout the auto layout for its pane count (and adopt its pattern), or clear it."""
        if self.auto_for(preset.pane_count) == preset and self.auto_source(preset.pane_count) == "chosen":
            del self.auto[preset.pane_count]
            if preset.family and preset.family == self.pattern:
                self.pattern = ""
            return False
        self.auto[preset.pane_count] = preset.id
        if preset.family:
            self.pattern = preset.family
        return True

    def duplicate_of(self, tree: Node) -> Optional[Preset]:
        return next((preset for preset in self.all() if same_shape(preset.tree, tree)), None)

    def add_saved(self, name: str, tree: Node) -> Preset:
        name = clean_name(name)
        validate_tree(tree)
        base = SAVED_PREFIX + slug(name)
        taken = {preset.id for preset in self.all()}
        identifier, suffix = base, 2
        while identifier in taken:
            identifier, suffix = "%s-%d" % (base, suffix), suffix + 1
        preset = Preset(identifier, name, validate_tree(tree), tree)
        self.saved.append(preset)
        return preset

    def rename_saved(self, preset_id: str, name: str) -> Preset:
        name = clean_name(name)
        for index, preset in enumerate(self.saved):
            if preset.id == preset_id:
                self.saved[index] = Preset(preset.id, name, preset.pane_count, preset.tree, preset.family)
                return self.saved[index]
        raise LibraryError("only saved layouts can be renamed")

    def delete_saved(self, preset_id: str) -> Preset:
        for index, preset in enumerate(self.saved):
            if preset.id == preset_id:
                del self.saved[index]
                if preset_id in self.favorites:
                    self.favorites.remove(preset_id)
                self.auto = {count: value for count, value in self.auto.items() if value != preset_id}
                return preset
        raise LibraryError("only saved layouts can be deleted")

    # Serialisation ---------------------------------------------------------

    def to_json(self) -> Dict[str, object]:
        return {
            "schema": 1,
            "favorites": list(self.favorites),
            "auto": {str(count): preset_id for count, preset_id in sorted(self.auto.items())},
            "pattern": self.pattern,
            "animation_ms": self.animation_ms,
            "saved": [{"id": preset.id, "name": preset.name, "tree": tree_to_json(preset.tree)}
                      for preset in self.saved],
        }

    @classmethod
    def load(cls, library_value: object, custom_value: object = None) -> "Library":
        library = cls()
        if isinstance(library_value, dict):
            for entry in library_value.get("saved", []) if isinstance(library_value.get("saved"), list) else []:
                try:
                    library.saved.append(preset_from_entry(entry, SAVED_PREFIX))
                except LibraryError as error:
                    library.problems.append("saved layout skipped: %s" % error)
            favorites = library_value.get("favorites", [])
            if isinstance(favorites, list):
                library.favorites = [value for value in favorites if isinstance(value, str)]
            auto = library_value.get("auto", {})
            if isinstance(auto, dict):
                library.auto = {int(count): value for count, value in auto.items()
                                if isinstance(count, str) and count.isdigit()
                                and 2 <= int(count) <= MAX_PANES and isinstance(value, str)}
            pattern = library_value.get("pattern", "")
            library.pattern = pattern if pattern in FAMILY_NAMES else ""
            animation = library_value.get("animation_ms", library.animation_ms)
            if isinstance(animation, int) and not isinstance(animation, bool):
                library.animation_ms = max(0, min(1000, animation))
        if custom_value is not None:
            entries = custom_value.get("layouts") if isinstance(custom_value, dict) else custom_value
            if not isinstance(entries, list):
                library.problems.append('layouts.json must hold a list or {"layouts": [...]}')
                entries = []
            for position, entry in enumerate(entries, 1):
                try:
                    library.custom.append(preset_from_entry(entry, CUSTOM_PREFIX))
                except LibraryError as error:
                    library.problems.append("layouts.json entry %d skipped: %s" % (position, error))
        seen = set()
        unique = []
        for preset in library.custom:
            if preset.id in seen:
                library.problems.append("layouts.json: duplicate name %s skipped" % preset.name)
                continue
            seen.add(preset.id)
            unique.append(preset)
        library.custom = unique
        known = {preset.id for preset in library.all()}
        library.favorites = [value for value in dict.fromkeys(library.favorites) if value in known]
        library.auto = {count: value for count, value in library.auto.items()
                        if value in known and library.find(value).pane_count == count}  # type: ignore[union-attr]
        return library


def parse_custom_file(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise LibraryError("layouts.json is not valid JSON (line %d)" % error.lineno) from error


def describe(preset: Preset) -> str:
    """A short generated description for layouts without a curated one."""
    tree = preset.tree
    if isinstance(tree, Leaf):
        return ""
    side = {"right": ("left", "right"), "down": ("top", "bottom")}[tree.direction]
    anchor_first = 0 in tuple(iter_leaf_indexes(tree.first))
    share = tree.ratio if anchor_first else 1 - tree.ratio
    others = preset.pane_count - 1
    return "Your pane takes the %s %d%%, %d other pane%s." % (
        side[0] if anchor_first else side[1], round(share * 100), others, "" if others == 1 else "s")
