from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Tuple, Union

from .catalog import MAX_PANES
from .model import Direction, Leaf, Node, Preset, Split


class PlanningError(ValueError):
    pass


@dataclass(frozen=True)
class PaneRect:
    pane_id: str
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class LayoutSnapshot:
    workspace_id: str
    tab_id: str
    active_pane_id: str
    area: Tuple[int, int, int, int]
    panes: Tuple[PaneRect, ...]
    raw_payload: Mapping[str, object]
    zoomed: bool = False


@dataclass(frozen=True)
class Move:
    pane_id: str
    target_pane_id: str
    direction: Direction
    ratio: float


@dataclass(frozen=True)
class Swap:
    source_pane_id: str
    target_pane_id: str


Operation = Union[Move, Swap]


def _required(mapping: Mapping[str, object], key: str, expected_type):
    value = mapping.get(key)
    if not isinstance(value, expected_type):
        raise PlanningError("layout field %s is missing or malformed" % key)
    return value


def snapshot_from_payload(
    payload: Mapping[str, object], active_pane_id: str, strict: bool = True
) -> LayoutSnapshot:
    """Parse a layout. Non-strict reads accept any pane count and zoom state for display."""
    try:
        result = _required(payload, "result", dict)
        layout = _required(result, "layout", dict)
        zoomed = layout.get("zoomed") is True
        if strict and zoomed:
            raise PlanningError("zoomed tabs cannot be rearranged")
        workspace_id = _required(layout, "workspace_id", str)
        tab_id = _required(layout, "tab_id", str)
        area_value = _required(layout, "area", dict)
        area = (
            int(_required(area_value, "x", int)),
            int(_required(area_value, "y", int)),
            int(_required(area_value, "width", int)),
            int(_required(area_value, "height", int)),
        )
        if area[2] <= 0 or area[3] <= 0:
            raise PlanningError("layout area is empty")
        pane_values = _required(layout, "panes", list)
        panes: List[PaneRect] = []
        for pane_value in pane_values:
            if not isinstance(pane_value, dict):
                raise PlanningError("pane entry is malformed")
            rect = _required(pane_value, "rect", dict)
            panes.append(
                PaneRect(
                    pane_id=_required(pane_value, "pane_id", str),
                    x=int(_required(rect, "x", int)),
                    y=int(_required(rect, "y", int)),
                    width=int(_required(rect, "width", int)),
                    height=int(_required(rect, "height", int)),
                )
            )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, PlanningError):
            raise
        raise PlanningError("layout payload is malformed") from exc
    if not panes or (strict and not 2 <= len(panes) <= MAX_PANES):
        raise PlanningError("layout must contain 2 to %d panes" % MAX_PANES)
    pane_ids = [pane.pane_id for pane in panes]
    if len(pane_ids) != len(set(pane_ids)):
        raise PlanningError("layout contains duplicate pane IDs")
    if active_pane_id not in pane_ids:
        raise PlanningError("active pane is not present in the source tab")
    return LayoutSnapshot(
        workspace_id=workspace_id,
        tab_id=tab_id,
        active_pane_id=active_pane_id,
        area=area,
        panes=tuple(panes),
        raw_payload=payload,
        zoomed=zoomed,
    )


def pane_ids_by_logical_index(snapshot: LayoutSnapshot) -> Tuple[str, ...]:
    active = next(pane for pane in snapshot.panes if pane.pane_id == snapshot.active_pane_id)
    others = sorted(
        (pane for pane in snapshot.panes if pane.pane_id != active.pane_id),
        key=lambda pane: (pane.y, pane.x, pane.pane_id),
    )
    return (active.pane_id,) + tuple(pane.pane_id for pane in others)


def _leaf_indexes(node: Node) -> Tuple[int, ...]:
    if isinstance(node, Leaf):
        return (node.index,)
    return _leaf_indexes(node.first) + _leaf_indexes(node.second)


IndexStep = Tuple[str, int, int, Direction, float]


def index_plan(preset: Preset) -> Tuple[IndexStep, ...]:
    """Build steps over logical leaf indexes, starting from the anchor alone.

    ("attach", child, parent, direction, ratio) places child beside parent;
    ("swap", a, b, ...) exchanges the two panes so the anchor lands second.
    """
    steps: List[IndexStep] = []

    def build(node: Node, root_leaf_index: int) -> None:
        if isinstance(node, Leaf):
            if node.index != root_leaf_index:
                raise PlanningError("preset root assignment is inconsistent")
            return
        first_indexes = _leaf_indexes(node.first)
        second_indexes = _leaf_indexes(node.second)
        if root_leaf_index in first_indexes:
            second_root = second_indexes[0]
            steps.append(("attach", second_root, root_leaf_index, node.direction, node.ratio))
            build(node.first, root_leaf_index)
            build(node.second, second_root)
            return
        if root_leaf_index in second_indexes:
            first_root = first_indexes[0]
            steps.append(("attach", first_root, root_leaf_index, node.direction, node.ratio))
            steps.append(("swap", root_leaf_index, first_root, node.direction, node.ratio))
            build(node.first, first_root)
            build(node.second, root_leaf_index)
            return
        raise PlanningError("preset does not contain root leaf")

    build(preset.tree, 0)
    return tuple(steps)


def plan_operations(snapshot: LayoutSnapshot, preset: Preset) -> Tuple[Operation, ...]:
    if len(snapshot.panes) != preset.pane_count:
        raise PlanningError(
            "layout %s requires %s panes; source tab has %s"
            % (preset.id, preset.pane_count, len(snapshot.panes))
        )
    pane_ids = pane_ids_by_logical_index(snapshot)
    operations: List[Operation] = []
    for kind, first, second, direction, ratio in index_plan(preset):
        if kind == "attach":
            operations.append(Move(pane_ids[first], pane_ids[second], direction, ratio))
        else:
            operations.append(Swap(pane_ids[first], pane_ids[second]))
    return tuple(operations)


def matching_preset_id(snapshot: LayoutSnapshot, presets: Iterable[Preset]) -> Optional[str]:
    """Return the preset the tab already shows, so the picker can mark it current.

    The anchor must sit in leaf 0; other panes may be in any order, because a
    preset built by splitting does not number panes in reading order.
    """
    actual = normalized_rectangles(snapshot)
    anchor = actual.get(snapshot.active_pane_id)
    others = sorted(rect for pane_id, rect in actual.items() if pane_id != snapshot.active_pane_id)

    def close(a, b, tolerance=0.03):
        return all(abs(x - y) <= tolerance for x, y in zip(a, b))

    for preset in presets:
        if preset.pane_count != len(snapshot.panes) or anchor is None:
            continue
        expected = expected_rectangles(preset)
        if not close(anchor, expected[0]):
            continue
        wanted = sorted(rect for index, rect in expected.items() if index != 0)
        if all(close(a, b) for a, b in zip(others, wanted)):
            return preset.id
    return None


def expected_rectangles(preset: Preset) -> Dict[int, Tuple[float, float, float, float]]:
    rectangles: Dict[int, Tuple[float, float, float, float]] = {}

    def visit(node: Node, x: float, y: float, width: float, height: float) -> None:
        if isinstance(node, Leaf):
            rectangles[node.index] = (round(x, 4), round(y, 4), round(width, 4), round(height, 4))
            return
        if node.direction == "right":
            first_width = width * node.ratio
            visit(node.first, x, y, first_width, height)
            visit(node.second, x + first_width, y, width - first_width, height)
        else:
            first_height = height * node.ratio
            visit(node.first, x, y, width, first_height)
            visit(node.second, x, y + first_height, width, height - first_height)

    visit(preset.tree, 0.0, 0.0, 1.0, 1.0)
    return rectangles


def normalized_rectangles(snapshot: LayoutSnapshot) -> Dict[str, Tuple[float, float, float, float]]:
    area_x, area_y, area_width, area_height = snapshot.area
    return {
        pane.pane_id: (
            round((pane.x - area_x) / area_width, 4),
            round((pane.y - area_y) / area_height, 4),
            round(pane.width / area_width, 4),
            round(pane.height / area_height, 4),
        )
        for pane in snapshot.panes
    }


def topology_matches(
    snapshot: LayoutSnapshot,
    preset: Preset,
    tolerance: float = 0.03,
    pane_ids: Tuple[str, ...] = (),
) -> bool:
    if not pane_ids:
        pane_ids = pane_ids_by_logical_index(snapshot)
    if len(pane_ids) != preset.pane_count:
        return False
    expected = expected_rectangles(preset)
    actual = normalized_rectangles(snapshot)
    for index, expected_rect in expected.items():
        actual_rect = actual.get(pane_ids[index])
        if actual_rect is None:
            return False
        if any(abs(want - got) > tolerance for want, got in zip(expected_rect, actual_rect)):
            return False
    return True


def preset_from_snapshot(snapshot: LayoutSnapshot) -> Preset:
    pane_ids = pane_ids_by_logical_index(snapshot)
    index_by_pane = {pane_id: index for index, pane_id in enumerate(pane_ids)}
    layout = snapshot.raw_payload["result"]["layout"]  # type: ignore[index]
    raw_splits = layout.get("splits", []) if isinstance(layout, dict) else []

    def bounds(panes: Tuple[PaneRect, ...]) -> Tuple[int, int, int, int]:
        left = min(pane.x for pane in panes)
        top = min(pane.y for pane in panes)
        right = max(pane.x + pane.width for pane in panes)
        bottom = max(pane.y + pane.height for pane in panes)
        return left, top, right - left, bottom - top

    def split_for_region(region: Tuple[int, int, int, int]):
        for raw_split in raw_splits:
            if not isinstance(raw_split, dict):
                continue
            rect = raw_split.get("rect")
            if not isinstance(rect, dict):
                continue
            candidate = (rect.get("x"), rect.get("y"), rect.get("width"), rect.get("height"))
            if candidate == region:
                direction = raw_split.get("direction")
                ratio = raw_split.get("ratio")
                if direction in ("right", "down") and isinstance(ratio, (int, float)):
                    return direction, float(ratio)
        return None

    def build(panes: Tuple[PaneRect, ...]) -> Node:
        if len(panes) == 1:
            return Leaf(index_by_pane[panes[0].pane_id])
        region = bounds(panes)
        raw = split_for_region(region)
        directions = (raw[0],) if raw else ("right", "down")
        for direction in directions:
            if direction == "right":
                candidates = sorted({pane.x for pane in panes if pane.x > region[0]})
                for boundary in candidates:
                    first = tuple(pane for pane in panes if pane.x + pane.width <= boundary)
                    second = tuple(pane for pane in panes if pane.x >= boundary)
                    if first and second and len(first) + len(second) == len(panes):
                        ratio = (boundary - region[0]) / region[2]
                        return Split("right", ratio, build(first), build(second))
            else:
                candidates = sorted({pane.y for pane in panes if pane.y > region[1]})
                for boundary in candidates:
                    first = tuple(pane for pane in panes if pane.y + pane.height <= boundary)
                    second = tuple(pane for pane in panes if pane.y >= boundary)
                    if first and second and len(first) + len(second) == len(panes):
                        ratio = (boundary - region[1]) / region[3]
                        return Split("down", ratio, build(first), build(second))
        raise PlanningError("could not reconstruct source split tree")

    return Preset("recovery-source", "Recovery Source", len(snapshot.panes), build(snapshot.panes))
