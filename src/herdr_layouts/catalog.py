from typing import Iterator, Optional, Sequence, Tuple

from .model import Leaf, Node, Preset, Split

MAX_PANES = 6


def _l(index: int) -> Leaf:
    return Leaf(index)


def _s(direction: str, ratio: float, first: Node, second: Node) -> Split:
    return Split(direction=direction, ratio=ratio, first=first, second=second)  # type: ignore[arg-type]


def _even(direction: str, nodes: Sequence[Node]) -> Node:
    """Equal shares in one direction: each split gives its first child 1/n of what is left."""
    if len(nodes) == 1:
        return nodes[0]
    return _s(direction, round(1 / len(nodes), 2), nodes[0], _even(direction, nodes[1:]))


def _leaves(*indexes: int) -> Tuple[Leaf, ...]:
    return tuple(_l(index) for index in indexes)


PRESETS: Tuple[Preset, ...] = (
    Preset("2-columns", "Columns", 2, _s("right", 0.50, _l(0), _l(1)), "columns"),
    Preset("2-rows", "Rows", 2, _s("down", 0.50, _l(0), _l(1)), "rows"),
    Preset("3-columns", "Three Columns", 3, _s("right", 0.33, _l(0), _s("right", 0.50, _l(1), _l(2))), "columns"),
    Preset("3-rows", "Three Rows", 3, _s("down", 0.33, _l(0), _s("down", 0.50, _l(1), _l(2))), "rows"),
    Preset("3-main-left", "Main Left", 3, _s("right", 0.60, _l(0), _s("down", 0.50, _l(1), _l(2))), "main-left"),
    Preset("3-main-right", "Main Right", 3, _s("right", 0.40, _s("down", 0.50, _l(1), _l(2)), _l(0)), "main-right"),
    Preset("3-main-top", "Main Top", 3, _s("down", 0.55, _l(0), _s("right", 0.50, _l(1), _l(2))), "main-top"),
    Preset("3-main-bottom", "Main Bottom", 3, _s("down", 0.45, _s("right", 0.50, _l(1), _l(2)), _l(0)), "main-bottom"),
    Preset("4-grid", "Grid", 4, _s("right", 0.50, _s("down", 0.50, _l(0), _l(1)), _s("down", 0.50, _l(2), _l(3))), "grid"),
    Preset("4-columns", "Four Columns", 4, _s("right", 0.25, _l(0), _s("right", 0.33, _l(1), _s("right", 0.50, _l(2), _l(3)))), "columns"),
    Preset("4-rows", "Four Rows", 4, _s("down", 0.25, _l(0), _s("down", 0.33, _l(1), _s("down", 0.50, _l(2), _l(3)))), "rows"),
    Preset("4-main-left", "Main Left Stack", 4, _s("right", 0.62, _l(0), _s("down", 0.33, _l(1), _s("down", 0.50, _l(2), _l(3)))), "main-left"),
    Preset("4-main-right", "Main Right Stack", 4, _s("right", 0.38, _s("down", 0.33, _l(1), _s("down", 0.50, _l(2), _l(3))), _l(0)), "main-right"),
    Preset("4-main-top", "Main Top Row", 4, _s("down", 0.60, _l(0), _s("right", 0.33, _l(1), _s("right", 0.50, _l(2), _l(3)))), "main-top"),
    Preset("4-main-bottom", "Main Bottom Row", 4, _s("down", 0.40, _s("right", 0.33, _l(1), _s("right", 0.50, _l(2), _l(3))), _l(0)), "main-bottom"),
    Preset("4-wide-left", "Wide Left Pair", 4, _s("right", 0.65, _s("down", 0.50, _l(0), _l(1)), _s("down", 0.50, _l(2), _l(3)))),
    Preset("4-wide-right", "Wide Right Pair", 4, _s("right", 0.35, _s("down", 0.50, _l(1), _l(2)), _s("down", 0.50, _l(0), _l(3)))),
    Preset("4-focus-grid", "Focus Grid", 4, _s("right", 0.65, _s("down", 0.65, _l(0), _l(1)), _s("down", 0.50, _l(2), _l(3))), "focus"),
    Preset("5-main-left", "Main Left", 5, _s("right", 0.60, _l(0), _even("down", _leaves(1, 2, 3, 4))), "main-left"),
    Preset("5-main-right", "Main Right", 5, _s("right", 0.40, _even("down", _leaves(1, 2, 3, 4)), _l(0)), "main-right"),
    Preset("5-main-top", "Main Top", 5, _s("down", 0.60, _l(0), _even("right", _leaves(1, 2, 3, 4))), "main-top"),
    Preset("5-main-bottom", "Main Bottom", 5, _s("down", 0.40, _even("right", _leaves(1, 2, 3, 4)), _l(0)), "main-bottom"),
    Preset("5-focus-grid", "Focus Grid", 5, _s("right", 0.55, _l(0), _s("right", 0.50, _s("down", 0.50, _l(1), _l(2)),
                                                                           _s("down", 0.50, _l(3), _l(4)))), "focus"),
    Preset("5-grid", "Two Over Three", 5, _s("down", 0.50, _s("right", 0.50, _l(0), _l(1)), _even("right", _leaves(2, 3, 4))), "grid"),
    Preset("5-columns", "Five Columns", 5, _even("right", _leaves(0, 1, 2, 3, 4)), "columns"),
    Preset("6-grid", "Grid 3 x 2", 6, _even("right", (_s("down", 0.50, _l(0), _l(1)), _s("down", 0.50, _l(2), _l(3)),
                                                        _s("down", 0.50, _l(4), _l(5)))), "grid"),
    Preset("6-grid-tall", "Grid 2 x 3", 6, _s("right", 0.50, _even("down", _leaves(0, 1, 2)), _even("down", _leaves(3, 4, 5)))),
    Preset("6-main-left", "Main Left", 6, _s("right", 0.55, _l(0), _even("down", _leaves(1, 2, 3, 4, 5))), "main-left"),
    Preset("6-main-right", "Main Right", 6, _s("right", 0.45, _even("down", _leaves(1, 2, 3, 4, 5)), _l(0)), "main-right"),
    Preset("6-main-top", "Main Top", 6, _s("down", 0.55, _l(0), _even("right", _leaves(1, 2, 3, 4, 5))), "main-top"),
    Preset("6-main-bottom", "Main Bottom", 6, _s("down", 0.45, _even("right", _leaves(1, 2, 3, 4, 5)), _l(0)), "main-bottom"),
    Preset("6-focus-grid", "Focus Grid", 6, _s("right", 0.50, _l(0), _s("down", 0.50, _even("right", _leaves(1, 2, 3)),
                                                                          _s("right", 0.50, _l(4), _l(5)))), "focus"),
)

# Where a pattern has no layout of its own at some pane count, the nearest
# relative stands in: "main left" with two panes is simply two columns.
FAMILY_FALLBACK = {
    "main-left": ("columns",),
    "main-right": ("columns",),
    "main-top": ("rows",),
    "main-bottom": ("rows",),
    "grid": ("main-left", "columns"),
    "focus": ("main-left", "columns"),
    # Six side-by-side columns or five stacked rows are too thin to work in.
    "columns": ("grid",),
    "rows": ("main-top", "grid"),
}

FAMILY_NAMES = {
    "columns": "Columns", "rows": "Rows", "main-left": "Main Left", "main-right": "Main Right",
    "main-top": "Main Top", "main-bottom": "Main Bottom", "grid": "Grid", "focus": "Focus Grid",
}


def presets_for_count(count: int) -> Tuple[Preset, ...]:
    return tuple(preset for preset in PRESETS if preset.pane_count == count)


def preset_by_id(preset_id: str) -> Preset:
    for preset in PRESETS:
        if preset.id == preset_id:
            return preset
    raise KeyError(preset_id)


def family_preset(family: str, count: int) -> Optional[Preset]:
    """The layout that represents a pattern at a pane count, following fallbacks."""
    for candidate in (family,) + FAMILY_FALLBACK.get(family, ()):
        found = next((preset for preset in PRESETS
                      if preset.family == candidate and preset.pane_count == count), None)
        if found is not None:
            return found
    return None


def iter_leaf_indexes(node: Node) -> Iterator[int]:
    if isinstance(node, Leaf):
        yield node.index
        return
    yield from iter_leaf_indexes(node.first)
    yield from iter_leaf_indexes(node.second)


def iter_ratios(node: Node) -> Iterator[float]:
    if isinstance(node, Leaf):
        return
    yield node.ratio
    yield from iter_ratios(node.first)
    yield from iter_ratios(node.second)
