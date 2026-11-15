from dataclasses import dataclass
from typing import Dict, List, Mapping, MutableMapping, Optional, Sequence

from .herdr import HerdrClient, HerdrError
from .model import Preset
from .planner import (
    LayoutSnapshot,
    Move,
    Swap,
    pane_ids_by_logical_index,
    plan_operations,
    preset_from_snapshot,
    snapshot_from_payload,
    topology_matches,
)
from .state import StateStore


class TransactionError(RuntimeError):
    pass


class RecoveryRequired(TransactionError):
    pass


@dataclass(frozen=True)
class ApplyResult:
    preset_id: str
    verified: bool


@dataclass(frozen=True)
class RecoveryResult:
    verified: bool


class LayoutTransaction:
    def __init__(self, client: HerdrClient, store: StateStore) -> None:
        self.client = client
        self.store = store

    @staticmethod
    def _new_journal(snapshot: LayoutSnapshot, preset: Preset) -> Dict[str, object]:
        return {
            "schema": 1,
            "source_tab_id": snapshot.tab_id,
            "active_pane_id": snapshot.active_pane_id,
            "source_payload": snapshot.raw_payload,
            "target_preset_id": preset.id,
            "phase": "staging",
            "completed": 0,
            "staged_panes": [],
        }

    def _checkpoint(self, journal: MutableMapping[str, object], phase: str) -> None:
        journal["phase"] = phase
        journal["completed"] = int(journal.get("completed", 0)) + 1
        self.store.write_journal(journal)

    def _stage_all(
        self,
        snapshot: LayoutSnapshot,
        journal: MutableMapping[str, object],
        only_panes: Optional[Sequence[str]] = None,
    ) -> None:
        staged = journal.setdefault("staged_panes", [])
        if not isinstance(staged, list):
            raise TransactionError("recovery journal staged_panes is malformed")
        wanted = list(only_panes or pane_ids_by_logical_index(snapshot)[1:])
        for pane_id in wanted:
            if pane_id in staged:
                continue
            result = self.client.detach(pane_id)
            staged.append(result.pane_id)
            self._checkpoint(journal, "staging")

    def _execute_plan(
        self,
        snapshot: LayoutSnapshot,
        preset: Preset,
        journal: MutableMapping[str, object],
    ) -> None:
        staged = journal.get("staged_panes")
        if not isinstance(staged, list):
            raise TransactionError("recovery journal staged_panes is malformed")
        for operation in plan_operations(snapshot, preset):
            if isinstance(operation, Move):
                if operation.pane_id not in staged:
                    raise TransactionError("pane %s is not staged" % operation.pane_id)
                result = self.client.insert(
                    operation.pane_id,
                    snapshot.tab_id,
                    operation.target_pane_id,
                    operation.direction,
                    operation.ratio,
                )
                if result.closed_tab_id is None:
                    raise TransactionError(
                        "Herdr did not confirm closure of the staging tab for %s"
                        % operation.pane_id
                    )
                staged.remove(operation.pane_id)
                self._checkpoint(journal, "inserting")
            else:
                self.client.swap(operation.source_pane_id, operation.target_pane_id)
                self._checkpoint(journal, "swapping")

    @staticmethod
    def _pane_ids_in_layout(payload: Mapping[str, object]) -> List[str]:
        try:
            panes = payload["result"]["layout"]["panes"]  # type: ignore[index]
        except (KeyError, TypeError) as exc:
            raise TransactionError("current layout response is malformed") from exc
        if not isinstance(panes, list):
            raise TransactionError("current layout response has invalid panes")
        pane_ids = []
        for pane in panes:
            if not isinstance(pane, dict) or not isinstance(pane.get("pane_id"), str):
                raise TransactionError("current layout contains an invalid pane")
            pane_ids.append(pane["pane_id"])
        return pane_ids

    def _rollback(self, journal: MutableMapping[str, object]) -> RecoveryResult:
        active_pane_id = journal.get("active_pane_id")
        source_payload = journal.get("source_payload")
        if not isinstance(active_pane_id, str) or not isinstance(source_payload, dict):
            raise RecoveryRequired("recovery journal lacks the source topology")
        original = snapshot_from_payload(source_payload, active_pane_id)
        source_preset = preset_from_snapshot(original)
        current_payload = self.client.layout(active_pane_id)
        current_panes = set(self._pane_ids_in_layout(current_payload))
        staged = journal.setdefault("staged_panes", [])
        if not isinstance(staged, list):
            raise RecoveryRequired("recovery journal staged pane list is malformed")
        panes_to_detach = [
            pane_id
            for pane_id in pane_ids_by_logical_index(original)[1:]
            if pane_id in current_panes and pane_id not in staged
        ]
        self._stage_all(original, journal, only_panes=panes_to_detach)
        self._execute_plan(original, source_preset, journal)
        restored = snapshot_from_payload(self.client.layout(active_pane_id), active_pane_id)
        if not topology_matches(
            restored,
            source_preset,
            pane_ids=pane_ids_by_logical_index(original),
        ):
            raise RecoveryRequired("rollback topology could not be verified")
        self.store.clear_journal()
        return RecoveryResult(True)

    def apply(self, snapshot: LayoutSnapshot, preset: Preset) -> ApplyResult:
        with self.store.tab_lock(snapshot.tab_id):
            if self.store.read_journal() is not None:
                raise RecoveryRequired("an unfinished layout transaction requires recovery")
            journal = self._new_journal(snapshot, preset)
            self.store.write_journal(journal)
            try:
                self._stage_all(snapshot, journal)
                self._execute_plan(snapshot, preset, journal)
                final_snapshot = snapshot_from_payload(
                    self.client.layout(snapshot.active_pane_id), snapshot.active_pane_id
                )
                if not topology_matches(
                    final_snapshot,
                    preset,
                    pane_ids=pane_ids_by_logical_index(snapshot),
                ):
                    raise TransactionError("applied layout did not match the selected preset")
                self.store.record_recent(preset.pane_count, preset.id)
                self.store.clear_journal()
                return ApplyResult(preset.id, True)
            except Exception as original_error:
                try:
                    self._rollback(journal)
                except Exception as rollback_error:
                    raise RecoveryRequired(
                        "layout failed and automatic rollback was not verified: %s"
                        % rollback_error
                    ) from original_error
                raise TransactionError("layout failed and was rolled back: %s" % original_error) from original_error

    def recover(self) -> RecoveryResult:
        journal = self.store.read_journal()
        if journal is None:
            return RecoveryResult(True)
        mutable_journal = dict(journal)
        source_tab_id = mutable_journal.get("source_tab_id")
        if not isinstance(source_tab_id, str):
            raise RecoveryRequired("recovery journal lacks source_tab_id")
        with self.store.tab_lock(source_tab_id):
            return self._rollback(mutable_journal)
