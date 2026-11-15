import json
import subprocess
import unittest

from herdr_layouts.herdr import HerdrClient, HerdrError


def move_payload(changed=True, reason=None, pane_id="w1:p2", tab_id="w1:t2"):
    result = {
        "changed": changed,
        "pane": {"pane_id": pane_id, "tab_id": tab_id},
    }
    if reason:
        result["reason"] = reason
    return json.dumps({"result": {"move_result": result}})


class RecordingRunner:
    def __init__(self, stdout="{}", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((tuple(args), kwargs))
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


class HerdrClientTests(unittest.TestCase):
    def test_split_uses_active_pane_without_shell_and_decodes_created_identity(self):
        runner = RecordingRunner(
            stdout=json.dumps({"result": {"pane": {"pane_id": "w1:p9", "tab_id": "w1:t1"}}})
        )
        created_pane_id = HerdrClient("herdr", runner=runner).split("w1:p2")
        self.assertEqual(created_pane_id, "w1:p9")
        self.assertEqual(
            runner.calls[0][0],
            ("herdr", "pane", "split", "w1:p2", "--direction", "right", "--ratio", "0.5000", "--no-focus"),
        )
        self.assertNotIn("shell", runner.calls[0][1])

    def test_pane_labels_lead_with_agent_state(self):
        payload = json.dumps({"result": {"panes": [
            {"pane_id": "p1", "tab_id": "t1", "agent": "claude",
             "agent_status": "blocked", "cwd": "/x/repo"},
            {"pane_id": "p2", "tab_id": "t1", "agent": None,
             "agent_status": None, "cwd": "/x/tmp"}]}})
        client = HerdrClient("herdr", runner=RecordingRunner(stdout=payload))
        self.assertEqual(client.pane_labels("t1"),
                         {"p1": "! claude · repo", "p2": "shell · tmp"})

    def test_refused_move_is_error_even_with_exit_zero(self):
        runner = RecordingRunner(stdout=move_payload(changed=False, reason="same_tab"))
        client = HerdrClient("herdr", runner=runner)
        with self.assertRaisesRegex(HerdrError, "same_tab"):
            client.insert("w1:p2", "w1:t1", "w1:p1", "right", 0.5)

    def test_detach_uses_fixed_argument_array_and_decodes_result(self):
        runner = RecordingRunner(stdout=move_payload())
        client = HerdrClient("/opt/homebrew/bin/herdr", runner=runner)
        result = client.detach("w1:p2")
        self.assertEqual(result.pane_id, "w1:p2")
        self.assertEqual(
            runner.calls[0][0],
            ("/opt/homebrew/bin/herdr", "pane", "move", "w1:p2", "--new-tab", "--no-focus"),
        )
        self.assertNotIn("shell", runner.calls[0][1])

    def test_nonzero_invalid_json_and_missing_fields_are_errors(self):
        for runner, message in (
            (RecordingRunner(returncode=1, stderr="server unavailable"), "server unavailable"),
            (RecordingRunner(stdout="not-json"), "invalid JSON"),
            (RecordingRunner(stdout='{"result": {}}'), "move_result"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(HerdrError, message):
                    HerdrClient("herdr", runner=runner).detach("w1:p2")

    def test_swap_uses_explicit_source_and_target(self):
        runner = RecordingRunner(stdout=json.dumps({"result": {"swap": {"changed": True}}}))
        HerdrClient("herdr", runner=runner).swap("w1:p1", "w1:p2")
        self.assertEqual(
            runner.calls[0][0],
            ("herdr", "pane", "swap", "--source-pane", "w1:p1", "--target-pane", "w1:p2"),
        )

    def test_swap_decodes_live_herdr_080_response_shape(self):
        runner = RecordingRunner(
            stdout=json.dumps(
                {
                    "id": "cli:pane:swap",
                    "result": {
                        "swap": {
                            "changed": True,
                            "source_pane_id": "w1:p1",
                            "target_pane_id": "w1:p2",
                        },
                        "type": "pane_swap",
                    },
                }
            )
        )
        HerdrClient("herdr", runner=runner).swap("w1:p1", "w1:p2")


if __name__ == "__main__":
    unittest.main()
