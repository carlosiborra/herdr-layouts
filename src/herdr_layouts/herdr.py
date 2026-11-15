import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence


class HerdrError(RuntimeError):
    pass


@dataclass(frozen=True)
class MoveResult:
    changed: bool
    pane_id: str
    tab_id: str
    closed_tab_id: Optional[str] = None


class HerdrClient:
    def __init__(
        self,
        binary: str = "herdr",
        timeout_seconds: float = 10.0,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self._runner = runner

    def _run(self, args: Sequence[str]) -> Mapping[str, Any]:
        argv = [self.binary] + list(args)
        try:
            completed = self._runner(
                argv,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HerdrError("HerdR command timed out") from exc
        except OSError as exc:
            raise HerdrError("could not execute HerdR: %s" % exc) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            raise HerdrError("HerdR command failed: %s" % detail)
        try:
            payload = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise HerdrError("HerdR returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HerdrError("HerdR response root is not an object")
        return payload

    @staticmethod
    def _result(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        result = payload.get("result")
        if not isinstance(result, dict):
            raise HerdrError("HerdR response is missing result")
        value = result.get(key)
        if not isinstance(value, dict):
            raise HerdrError("HerdR response is missing %s" % key)
        return value

    @classmethod
    def _move_result(cls, payload: Mapping[str, Any]) -> MoveResult:
        result = cls._result(payload, "move_result")
        if result.get("changed") is not True:
            reason = result.get("reason", "operation was refused")
            raise HerdrError("HerdR refused pane move: %s" % reason)
        pane = result.get("pane")
        if not isinstance(pane, dict):
            raise HerdrError("HerdR move_result is missing pane")
        pane_id = pane.get("pane_id")
        tab_id = pane.get("tab_id")
        if not isinstance(pane_id, str) or not isinstance(tab_id, str):
            raise HerdrError("HerdR move_result contains invalid pane identity")
        closed_tab_id = result.get("closed_tab_id")
        if closed_tab_id is not None and not isinstance(closed_tab_id, str):
            raise HerdrError("HerdR move_result contains invalid closed tab ID")
        return MoveResult(True, pane_id, tab_id, closed_tab_id)

    def pane_labels(self, tab_id: str) -> Dict[str, str]:
        """Short display labels per pane: agent state and name, else the shell, plus its folder.

        Terminal titles are deliberately ignored; they can hold conversation
        topics, and labels are shown only, never stored.
        """
        payload = self._run(("pane", "list"))
        result = payload.get("result")
        panes = result.get("panes") if isinstance(result, dict) else None
        labels: Dict[str, str] = {}
        for pane in panes if isinstance(panes, list) else []:
            if not isinstance(pane, dict) or pane.get("tab_id") != tab_id:
                continue
            pane_id = pane.get("pane_id")
            if not isinstance(pane_id, str):
                continue
            agent = pane.get("agent")
            cwd = pane.get("foreground_cwd") or pane.get("cwd")
            folder = cwd.rstrip("/").rsplit("/", 1)[-1] if isinstance(cwd, str) and cwd.strip("/") else ""
            name = agent if isinstance(agent, str) and agent else "shell"
            status = pane.get("agent_status") if isinstance(agent, str) and agent else None
            glyph = {"blocked": "!", "done": "✓", "working": "◴", "idle": "○",
                     "unknown": "?"}.get(status, "")
            if glyph:
                name = glyph + " " + name
            labels[pane_id] = "%s · %s" % (name, folder) if folder else name
        return labels

    def layout(self, pane_id: str) -> Mapping[str, Any]:
        return self._run(("pane", "layout", "--pane", pane_id))

    def split(
        self, pane_id: str, direction: str = "right", ratio: float = 0.5
    ) -> str:
        payload = self._run(
            (
                "pane",
                "split",
                pane_id,
                "--direction",
                direction,
                "--ratio",
                "%.4f" % ratio,
                "--no-focus",
            )
        )
        pane = self._result(payload, "pane")
        pane_id = pane.get("pane_id")
        if not isinstance(pane_id, str):
            raise HerdrError("HerdR split response contains invalid pane identity")
        return pane_id

    def detach(self, pane_id: str) -> MoveResult:
        payload = self._run(("pane", "move", pane_id, "--new-tab", "--no-focus"))
        result = self._move_result(payload)
        if result.pane_id != pane_id:
            raise HerdrError("HerdR changed pane ID while detaching %s" % pane_id)
        return result

    def insert(
        self,
        pane_id: str,
        tab_id: str,
        target_pane_id: str,
        direction: str,
        ratio: float,
    ) -> MoveResult:
        payload = self._run(
            (
                "pane",
                "move",
                pane_id,
                "--tab",
                tab_id,
                "--target-pane",
                target_pane_id,
                "--split",
                direction,
                "--ratio",
                "%.4f" % ratio,
                "--no-focus",
            )
        )
        result = self._move_result(payload)
        if result.pane_id != pane_id or result.tab_id != tab_id:
            raise HerdrError("HerdR inserted pane into an unexpected destination")
        return result

    def swap(self, source_pane_id: str, target_pane_id: str) -> None:
        payload = self._run(
            (
                "pane",
                "swap",
                "--source-pane",
                source_pane_id,
                "--target-pane",
                target_pane_id,
            )
        )
        result = self._result(payload, "swap")
        if result.get("changed") is not True:
            reason = result.get("reason", "operation was refused")
            raise HerdrError("HerdR refused pane swap: %s" % reason)

    def unzoom(self, pane_id: str) -> None:
        self._run(("pane", "zoom", pane_id, "--off"))

    def close(self, pane_id: str) -> None:
        self._run(("pane", "close", pane_id))
