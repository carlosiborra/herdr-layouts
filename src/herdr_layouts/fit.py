"""Fit a tab's existing split tree to a target layout by changing ratios only.

Herdr exports a tab as a binary split tree. Many trees can draw the same
picture: three columns may be right(a, right(b, c)) or right(right(a, b), c).
Instead of moving panes to rebuild the preset's exact tree, this module asks
whether the tree the tab already has can reach the target rectangles, and if
so with which ratios. Adjusting ratios never moves, closes or restarts a pane,
and can be animated.

Paths follow Herdr's layout.set_split_ratio: a list of booleans from the root,
False for the first child and True for the second.
"""
import time
from dataclasses import dataclass
from itertools import permutations
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

Rect = Tuple[float, float, float, float]
Path = Tuple[bool, ...]

# Herdr clamps split ratios to this range.
MIN_RATIO, MAX_RATIO = 0.1, 0.9
TOLERANCE = 0.004


class FitError(ValueError):
    pass


@dataclass(frozen=True)
class Step:
    path: Path
    start: float
    end: float


@dataclass(frozen=True)
class Fit:
    steps: Tuple[Step, ...]
    assignment: Dict[str, int]  # pane ID -> target leaf index (0 is the main pane)
    cost: float

    @property
    def changes(self) -> Tuple[Step, ...]:
        return tuple(step for step in self.steps if abs(step.end - step.start) > 0.005)


def leaves(node: Mapping[str, object]) -> List[str]:
    if node.get("type") == "pane":
        pane_id = node.get("pane_id")
        if not isinstance(pane_id, str):
            raise FitError("layout pane is missing its ID")
        return [pane_id]
    if node.get("type") != "split":
        raise FitError("layout node has an unknown type")
    return leaves(node["first"]) + leaves(node["second"])  # type: ignore[arg-type]


def rectangles(node: Mapping[str, object], ratios: Optional[Mapping[Path, float]] = None,
               region: Rect = (0.0, 0.0, 1.0, 1.0), path: Path = ()) -> Dict[str, Rect]:
    """Normalised rectangle per pane, using override ratios where given."""
    if node.get("type") == "pane":
        return {str(node["pane_id"]): region}
    ratio = float(ratios[path]) if ratios and path in ratios else float(node["ratio"])  # type: ignore[arg-type]
    x, y, width, height = region
    if node["direction"] == "right":
        first = (x, y, width * ratio, height)
        second = (x + width * ratio, y, width * (1 - ratio), height)
    else:
        first = (x, y, width, height * ratio)
        second = (x, y + height * ratio, width, height * (1 - ratio))
    result = rectangles(node["first"], ratios, first, path + (False,))  # type: ignore[arg-type]
    result.update(rectangles(node["second"], ratios, second, path + (True,)))  # type: ignore[arg-type]
    return result


def split_paths(node: Mapping[str, object], path: Path = ()) -> Dict[Path, float]:
    if node.get("type") != "split":
        return {}
    found = {path: float(node["ratio"])}  # type: ignore[arg-type]
    found.update(split_paths(node["first"], path + (False,)))  # type: ignore[arg-type]
    found.update(split_paths(node["second"], path + (True,)))  # type: ignore[arg-type]
    return found


def _close(a: Rect, b: Rect) -> bool:
    return all(abs(p - q) <= TOLERANCE for p, q in zip(a, b))


def _solve(node: Mapping[str, object], region: Rect, wanted: Mapping[str, Rect], path: Path,
           out: Dict[Path, float]) -> bool:
    """Find ratios that place every pane of this subtree on its wanted rectangle."""
    if node.get("type") == "pane":
        return _close(region, wanted[str(node["pane_id"])])
    first_panes = leaves(node["first"])  # type: ignore[arg-type]
    x, y, width, height = region
    if node["direction"] == "right":
        boundary = max(wanted[pane][0] + wanted[pane][2] for pane in first_panes)
        ratio = (boundary - x) / width if width else 0.0
        first = (x, y, boundary - x, height)
        second = (boundary, y, x + width - boundary, height)
    else:
        boundary = max(wanted[pane][1] + wanted[pane][3] for pane in first_panes)
        ratio = (boundary - y) / height if height else 0.0
        first = (x, y, width, boundary - y)
        second = (x, boundary, width, y + height - boundary)
    if not MIN_RATIO - 1e-9 <= ratio <= MAX_RATIO + 1e-9:
        return False
    out[path] = round(ratio, 4)
    return (_solve(node["first"], first, wanted, path + (False,), out)  # type: ignore[arg-type]
            and _solve(node["second"], second, wanted, path + (True,), out))  # type: ignore[arg-type]


def fit(root: Mapping[str, object], target: Mapping[int, Rect],
        main_pane: Optional[str] = None) -> Optional[Fit]:
    """Ratios that turn ``root`` into ``target``, or None when panes would have to move.

    Among every way of assigning panes to target rectangles, pick the one that
    changes the picture least, so the biggest pane stays biggest. When
    ``main_pane`` is given, only assignments that put it on leaf 0 are allowed.
    """
    panes = leaves(root)
    if len(panes) != len(target) or len(panes) > 6:
        return None
    current = rectangles(root)
    before = split_paths(root)
    best: Optional[Fit] = None
    for order in permutations(sorted(target)):
        assignment = dict(zip(panes, order))
        if main_pane is not None and assignment.get(main_pane) != 0:
            continue
        wanted = {pane: target[index] for pane, index in assignment.items()}
        ratios: Dict[Path, float] = {}
        if not _solve(root, (0.0, 0.0, 1.0, 1.0), wanted, (), ratios):
            continue
        cost = sum(abs(p - q) for pane in panes for p, q in zip(current[pane], wanted[pane]))
        if best is None or cost < best.cost - 1e-9:
            steps = tuple(Step(path, before[path], ratios[path]) for path in sorted(ratios, key=len))
            best = Fit(steps, assignment, cost)
    return best


def grow_from(steps: Sequence[Step], root: Mapping[str, object], new_pane: str) -> Tuple[Step, ...]:
    """Start the split that holds a new pane from almost closed, so the pane grows in."""
    location = _parent_of(root, new_pane, ())
    if location is None:
        return tuple(steps)
    path, is_second = location
    start = MAX_RATIO if is_second else MIN_RATIO
    adjusted = []
    found = False
    for step in steps:
        if step.path == path:
            adjusted.append(Step(path, start, step.end))
            found = True
        else:
            adjusted.append(step)
    if not found:
        ratio = split_paths(root).get(path)
        if ratio is not None:
            adjusted.append(Step(path, start, ratio))
    return tuple(adjusted)


def _parent_of(node: Mapping[str, object], pane_id: str, path: Path) -> Optional[Tuple[Path, bool]]:
    if node.get("type") != "split":
        return None
    for is_second, child in ((False, node["first"]), (True, node["second"])):
        if child.get("type") == "pane" and child.get("pane_id") == pane_id:  # type: ignore[union-attr]
            return path, is_second
        found = _parent_of(child, pane_id, path + (is_second,))  # type: ignore[arg-type]
        if found is not None:
            return found
    return None


def unfold(root: Mapping[str, object], pane_id: str) -> Tuple[Step, ...]:
    """Steps that open every split outward from one pane, as if the layout grew out of it."""
    steps = []

    def visit(node: Mapping[str, object], path: Path) -> None:
        if node.get("type") != "split":
            return
        ratio = float(node["ratio"])  # type: ignore[arg-type]
        if pane_id in leaves(node["first"]):  # type: ignore[arg-type]
            steps.append(Step(path, MAX_RATIO, ratio))
        elif pane_id in leaves(node["second"]):  # type: ignore[arg-type]
            steps.append(Step(path, MIN_RATIO, ratio))
        visit(node["first"], path + (False,))  # type: ignore[arg-type]
        visit(node["second"], path + (True,))  # type: ignore[arg-type]

    visit(root, ())
    return tuple(steps)


def balance(root: Mapping[str, object]) -> Tuple[Step, ...]:
    """Steps that give every pane an equal share along each run of same-direction splits."""
    steps = []

    def weight(node: Mapping[str, object], direction: str) -> int:
        if node.get("type") == "split" and node.get("direction") == direction:
            return weight(node["first"], direction) + weight(node["second"], direction)  # type: ignore[arg-type]
        return 1

    def visit(node: Mapping[str, object], path: Path) -> None:
        if node.get("type") != "split":
            return
        direction = str(node["direction"])
        first = weight(node["first"], direction)  # type: ignore[arg-type]
        second = weight(node["second"], direction)  # type: ignore[arg-type]
        target = min(MAX_RATIO, max(MIN_RATIO, first / (first + second)))
        steps.append(Step(path, float(node["ratio"]), round(target, 4)))  # type: ignore[arg-type]
        visit(node["first"], path + (False,))  # type: ignore[arg-type]
        visit(node["second"], path + (True,))  # type: ignore[arg-type]

    visit(root, ())
    return tuple(steps)


EASINGS: Dict[str, Callable[[float], float]] = {
    "linear": lambda t: t,
    "out-cubic": lambda t: 1 - (1 - t) ** 3,
    "out-quint": lambda t: 1 - (1 - t) ** 5,
    # A small overshoot that settles back, like a spring.
    "out-back": lambda t: 1 + 2.2 * (t - 1) ** 3 + 1.2 * (t - 1) ** 2,
}


def ease_out_cubic(t: float) -> float:
    return EASINGS["out-cubic"](t)


def at(steps: Sequence[Step], progress: float, easing: str = "out-cubic") -> List[Tuple[Path, float]]:
    curve = EASINGS.get(easing, ease_out_cubic)
    t = curve(min(1.0, max(0.0, progress)))
    return [(step.path, round(min(MAX_RATIO, max(MIN_RATIO, step.start + (step.end - step.start) * t)), 4))
            for step in steps]


def frames(steps: Sequence[Step], count: int, easing: str = "out-cubic") -> List[List[Tuple[Path, float]]]:
    """Per-frame ratio settings; the final frame lands exactly on each end value."""
    count = max(1, count)
    return [at(steps, frame / count, easing) for frame in range(1, count + 1)]


def play(set_ratio: Callable[[Path, float], None], steps: Sequence[Step], duration: float,
         easing: str = "out-cubic", clock: Callable[[], float] = time.monotonic,
         sleep: Callable[[float], None] = time.sleep, frame_seconds: float = 1 / 60) -> int:
    """Drive the animation by wall time, so slow writes drop frames instead of slowing it down.

    Parent splits are written before children, and the last frame always lands on
    the exact targets. Returns the number of frames written.
    """
    ordered = sorted(steps, key=lambda step: len(step.path))
    if not ordered:
        return 0
    written = 0
    if duration > 0:
        for path, ratio in at(ordered, 0.0, easing):
            set_ratio(path, ratio)
        started = clock()
        while True:
            progress = (clock() - started) / duration
            if progress >= 1:
                break
            for path, ratio in at(ordered, progress, easing):
                set_ratio(path, ratio)
            written += 1
            sleep(frame_seconds)
    for step in ordered:
        set_ratio(step.path, min(MAX_RATIO, max(MIN_RATIO, step.end)))
    return written + 1
