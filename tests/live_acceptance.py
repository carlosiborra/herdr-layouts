#!/usr/bin/env python3
import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from herdr_layouts.catalog import MAX_PANES, presets_for_count
from herdr_layouts.herdr import HerdrClient
from herdr_layouts.planner import snapshot_from_payload
from herdr_layouts.state import StateStore
from herdr_layouts.transaction import LayoutTransaction


class SessionRunner:
    def __init__(self, session):
        self.session = session

    def __call__(self, args, **kwargs):
        session_args = [args[0], "--session", self.session] + list(args[1:])
        return subprocess.run(session_args, **kwargs)


def run_json(binary, session, args):
    completed = subprocess.run(
        [binary, "--session", session] + list(args),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


def pane_id_from_split(payload):
    return payload["result"]["pane"]["pane_id"]


def identities(binary, session, tab_id):
    payload = run_json(binary, session, ("pane", "list"))
    return {
        pane["pane_id"]: (pane["terminal_id"], pane["cwd"])
        for pane in payload["result"]["panes"]
        if pane["tab_id"] == tab_id
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--active-pane", required=True)
    parser.add_argument("--binary", default="herdr")
    args = parser.parse_args()

    client = HerdrClient(args.binary, runner=SessionRunner(args.session))
    active_pane_id = args.active_pane
    initial = snapshot_from_payload(client.layout(active_pane_id), active_pane_id, strict=False)
    workspace_id = initial.workspace_id
    tab_id = initial.tab_id
    created_panes = []
    results = []

    with tempfile.TemporaryDirectory(prefix="herdr-layouts-acceptance-") as state_dir:
        store = StateStore(Path(state_dir))
        transaction = LayoutTransaction(client, store)
        for pane_count in range(2, MAX_PANES + 1):
            current = snapshot_from_payload(client.layout(active_pane_id), active_pane_id, strict=False)
            while len(current.panes) < pane_count:
                payload = run_json(
                    args.binary,
                    args.session,
                    (
                        "pane",
                        "split",
                        active_pane_id,
                        "--direction",
                        "right" if len(current.panes) % 2 else "down",
                        "--cwd",
                        str(Path(__file__).resolve().parents[1]),
                        "--no-focus",
                    ),
                )
                created_panes.append(pane_id_from_split(payload))
                current = snapshot_from_payload(client.layout(active_pane_id), active_pane_id, strict=False)

            before_identities = identities(args.binary, args.session, tab_id)
            for preset in presets_for_count(pane_count):
                snapshot = snapshot_from_payload(client.layout(active_pane_id), active_pane_id)
                try:
                    applied = transaction.apply(snapshot, preset)
                except Exception as exc:
                    measured = client.layout(active_pane_id)
                    raise RuntimeError(
                        "preset %s failed: %s; measured=%s"
                        % (preset.id, exc, json.dumps(measured, sort_keys=True))
                    ) from exc
                after = snapshot_from_payload(client.layout(active_pane_id), active_pane_id)
                after_identities = identities(args.binary, args.session, tab_id)
                tabs = run_json(
                    args.binary,
                    args.session,
                    ("tab", "list", "--workspace", workspace_id),
                )["result"]["tabs"]
                if not applied.verified:
                    raise AssertionError("preset %s was not verified" % preset.id)
                if before_identities != after_identities:
                    raise AssertionError("pane identity changed for %s" % preset.id)
                if after.active_pane_id != active_pane_id:
                    raise AssertionError("active pane changed for %s" % preset.id)
                if len(tabs) != 1:
                    raise AssertionError("staging tab remained after %s" % preset.id)
                if store.read_journal() is not None:
                    raise AssertionError("journal remained after %s" % preset.id)
                results.append(preset.id)

    print(
        json.dumps(
            {
                "session": args.session,
                "workspace_id": workspace_id,
                "tab_id": tab_id,
                "active_pane_id": active_pane_id,
                "created_panes": created_panes,
                "verified_presets": results,
                "count": len(results),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
