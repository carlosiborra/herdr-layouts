"""Reach a layout by easing split ratios, and play layout animations after the popup closes.

The picker popup covers most of the tab, so an animation played while it is
open would be hidden. Instead the picker hands the animation to a detached
copy of itself (``--play``) and exits; the player waits for the popup to
close, then eases the ratios while holding the tab's busy lock.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .fit import Fit, Step, fit, play, unfold
from .herdr import HerdrError
from .model import Preset
from .planner import expected_rectangles
from .rpc import Rpc
from .state import StateStore

# Long enough for Herdr to close the popup before the first frame.
POPUP_CLOSE_DELAY = 0.08
EASING = "out-cubic"


def fit_preset(rpc: Rpc, tab_id: str, preset: Preset, main_pane: Optional[str]) -> Optional[Fit]:
    """Ratios that turn the tab into ``preset`` without moving a pane, or None."""
    layout = rpc.export(tab_id)
    if layout.get("zoomed"):
        return None
    return fit(layout["root"], expected_rectangles(preset), main_pane)


def unfold_steps(rpc: Rpc, tab_id: str, pane_id: str) -> Sequence[Step]:
    return unfold(rpc.export(tab_id)["root"], pane_id)


def encode(tab_id: str, steps: Sequence[Step], duration_ms: int, delay: float = POPUP_CLOSE_DELAY) -> str:
    return json.dumps({"tab_id": tab_id, "ms": duration_ms, "delay": delay,
                       "steps": [[list(step.path), step.start, step.end] for step in steps]})


def decode(value: str):
    payload = json.loads(value)
    steps = tuple(Step(tuple(bool(part) for part in path), float(start), float(end))
                  for path, start, end in payload["steps"])
    delay = min(1.0, max(0.0, float(payload.get("delay", POPUP_CLOSE_DELAY))))
    return str(payload["tab_id"]), steps, int(payload["ms"]), delay


def run_steps(rpc: Rpc, store: StateStore, tab_id: str, steps: Sequence[Step], duration_ms: int) -> int:
    with store.busy(tab_id):
        return play(lambda path, ratio: rpc.set_ratio(tab_id, path, ratio), steps,
                    duration_ms / 1000.0, EASING)


def hand_off(tab_id: str, steps: Sequence[Step], duration_ms: int, environ: Mapping[str, str],
             entry: Path, rpc: Rpc, delay: float = POPUP_CLOSE_DELAY) -> None:
    """Start the detached player; without one, land on the final ratios at once."""
    if not steps:
        return
    if duration_ms > 0:
        try:
            subprocess.Popen(
                [sys.executable, str(entry), "--play", encode(tab_id, steps, duration_ms, delay)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, env=dict(environ), close_fds=True,
            )
            return
        except OSError:
            pass
    for step in sorted(steps, key=lambda item: len(item.path)):
        rpc.set_ratio(tab_id, step.path, step.end)


def play_main(value: str, rpc: Rpc, store: StateStore) -> int:
    try:
        tab_id, steps, duration_ms, delay = decode(value)
    except (ValueError, KeyError, TypeError):
        return 2
    time.sleep(delay)
    try:
        run_steps(rpc, store, tab_id, steps, duration_ms)
    except HerdrError:
        # The tab may have closed meanwhile; the layout itself is already in place.
        return 1
    return 0

