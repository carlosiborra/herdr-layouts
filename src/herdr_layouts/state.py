import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Mapping, Optional, Tuple

from .catalog import PRESETS
from .library import Library, LibraryError, parse_custom_file


def _touch(path: Path) -> None:
    try:
        os.utime(path)
    except OSError:
        pass


class StateError(RuntimeError):
    pass


class TabLockedError(StateError):
    pass


class StateStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._held_locks = set()
        # IDs recents may hold; the app widens this to saved and custom layouts.
        self.known_ids = {preset.id for preset in PRESETS}
        self.pane_counts = {preset.id: preset.pane_count for preset in PRESETS}

    def register(self, presets) -> None:
        for preset in presets:
            self.known_ids.add(preset.id)
            self.pane_counts[preset.id] = preset.pane_count

    @property
    def library_path(self) -> Path:
        return self.root / "library.json"

    @property
    def custom_path(self) -> Path:
        return self.root / "layouts.json"

    def load_library(self) -> Library:
        custom = None
        problems = []
        try:
            text = self.custom_path.read_text(encoding="utf-8")
            custom = parse_custom_file(text)
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError) as exc:
            problems.append("could not read layouts.json: %s" % exc)
        except LibraryError as exc:
            problems.append(str(exc))
        library = Library.load(self._read_json(self.library_path, corrupt_as_empty=True), custom)
        library.problems[:0] = problems
        self.register(library.all())
        return library

    def save_library(self, library: Library) -> None:
        self._atomic_json(self.library_path, library.to_json())
        self.register(library.all())

    def prune_locks(self, max_age_seconds: float = 86400.0, now: Optional[float] = None) -> int:
        """Remove tab lock files untouched for a day.

        A transaction finishes in well under a second, so a day-old lock file
        cannot belong to a running picker; deleting only old files avoids two
        pickers ever locking different inodes for the same tab.
        """
        now = time.time() if now is None else now
        removed = 0
        try:
            candidates = list(self.root.glob("tab-*.lock")) + list(self.root.glob("busy-*.lock"))
        except OSError:
            return 0
        for path in candidates:
            try:
                if now - path.stat().st_mtime > max_age_seconds:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def journal_path(self) -> Path:
        return self.root / "recovery-journal.json"

    def _read_json(self, path: Path, corrupt_as_empty: bool = False):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            if corrupt_as_empty:
                return None
            raise StateError("could not read %s: %s" % (path.name, exc)) from exc

    def _atomic_json(self, path: Path, value: Mapping[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        file_descriptor, temp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(self.root)
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

    def _state(self) -> Dict[str, object]:
        value = self._read_json(self.state_path, corrupt_as_empty=True)
        if not isinstance(value, dict):
            return {"schema": 1, "recents": {}}
        recents = value.get("recents")
        if not isinstance(recents, dict):
            return {"schema": 1, "recents": {}}
        last = value.get("last")
        state: Dict[str, object] = {"schema": 1, "recents": recents}
        if isinstance(last, str):
            state["last"] = last
        return state

    def recent(self, pane_count: int) -> Tuple[str, ...]:
        value = self._state().get("recents", {})
        if not isinstance(value, dict):
            return ()
        candidates = value.get(str(pane_count), [])
        if not isinstance(candidates, list):
            return ()
        valid_ids = {pid for pid in self.known_ids if self.pane_counts.get(pid) == pane_count}
        return tuple(
            preset_id
            for preset_id in candidates
            if isinstance(preset_id, str) and preset_id in valid_ids
        )[:3]

    def record_recent(self, pane_count: int, preset_id: str) -> None:
        if self.pane_counts.get(preset_id) != pane_count:
            raise StateError("preset %s is not valid for %s panes" % (preset_id, pane_count))
        state = self._state()
        recents = state["recents"]
        assert isinstance(recents, dict)
        prior = self.recent(pane_count)
        recents[str(pane_count)] = [preset_id] + [item for item in prior if item != preset_id][:2]
        state["last"] = preset_id
        self._atomic_json(self.state_path, state)

    def last(self) -> Optional[str]:
        """The most recently applied preset across pane counts."""
        value = self._state().get("last")
        return value if isinstance(value, str) and value in self.known_ids else None

    def write_journal(self, journal: Mapping[str, object]) -> None:
        if journal.get("schema") != 1:
            raise StateError("unsupported journal schema")
        self._atomic_json(self.journal_path, journal)

    def read_journal(self) -> Optional[Mapping[str, object]]:
        value = self._read_json(self.journal_path)
        if value is None:
            return None
        if not isinstance(value, dict) or value.get("schema") != 1:
            raise StateError("recovery journal is malformed or unsupported")
        return value

    def clear_journal(self) -> None:
        try:
            self.journal_path.unlink()
        except FileNotFoundError:
            pass

    def busy_path(self, tab_id: str) -> Path:
        safe_id = "".join(character if character.isalnum() else "_" for character in tab_id)
        return self.root / ("busy-%s.lock" % safe_id)

    @contextmanager
    def busy(self, tab_id: str) -> Iterator[None]:
        """Advertise a layout change in progress, held through any animation.

        Effects plugins take a shared lock on this file without blocking and stay
        out of the way while it is held. It never blocks the picker itself.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.busy_path(tab_id)
        handle = path.open("a+")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            _touch(path)
            yield
        finally:
            _touch(path)
            handle.close()

    @contextmanager
    def tab_lock(self, tab_id: str) -> Iterator[None]:
        safe_id = "".join(character if character.isalnum() else "_" for character in tab_id)
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ("tab-%s.lock" % safe_id)
        if lock_path in self._held_locks:
            raise TabLockedError("another layout change already owns tab %s" % tab_id)
        handle = lock_path.open("a+")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise TabLockedError("another layout change already owns tab %s" % tab_id) from exc
            self._held_locks.add(lock_path)
            _touch(lock_path)
            yield
        finally:
            if lock_path in self._held_locks:
                # The modification time tells effects plugins a change just finished.
                _touch(lock_path)
                self._held_locks.remove(lock_path)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
