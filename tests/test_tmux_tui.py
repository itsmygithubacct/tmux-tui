from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tmux_tui", ROOT / "tmux_tui.py")
assert SPEC and SPEC.loader
tmux_tui = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tmux_tui
SPEC.loader.exec_module(tmux_tui)


class HelpersTests(unittest.TestCase):
    def test_group_panes_uses_numeric_window_and_pane_order(self):
        panes = [
            {"session": "work", "window": "10", "pane": "0"},
            {"session": "work", "window": "2", "pane": "1"},
            {"session": "other", "window": "0", "pane": "0"},
            {"session": "work", "window": "2", "pane": "0"},
        ]
        grouped = tmux_tui.group_panes(panes)
        self.assertEqual(
            [(row["window"], row["pane"]) for row in grouped["work"]],
            [("2", "0"), ("2", "1"), ("10", "0")],
        )

    def test_pane_target_is_stable_tmux_cli_syntax(self):
        self.assertEqual(
            tmux_tui.pane_target(
                {"session": "build", "window": "3", "pane": "2"}
            ),
            "build:3.2",
        )

    def test_preview_removes_terminal_controls(self):
        lines = tmux_tui.clean_preview("\x1b[31mred\x1b[0m\tok\nbad\x01")
        self.assertEqual(lines, ["red ok", "bad�"])

    def test_age_format(self):
        self.assertEqual(tmux_tui.format_age(-5), "0s")
        self.assertEqual(tmux_tui.format_age(90), "1m")
        self.assertEqual(tmux_tui.format_age(7200), "2h")
        self.assertEqual(tmux_tui.format_age(172800), "2d")


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.backend = tmux_tui.TmuxCLI(["python3", "/opt/tmux-cli/tb.py"])

    def completed(self, *, stdout="", stderr="", returncode=0):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)

    @mock.patch.object(tmux_tui.subprocess, "run")
    def test_snapshot_uses_json_contract(self, run):
        payload = {"sessions": [], "panes": [], "tmux_server": False}
        run.return_value = self.completed(
            stdout=json.dumps({"ok": True, "data": payload}) + "\n"
        )
        self.assertEqual(self.backend.snapshot(), payload)
        argv = run.call_args.args[0]
        self.assertEqual(
            argv,
            ["python3", "/opt/tmux-cli/tb.py", "--json", "snapshot"],
        )
        self.assertEqual(run.call_args.kwargs["env"]["NO_COLOR"], "1")

    @mock.patch.object(tmux_tui.subprocess, "run")
    def test_mutations_go_through_tb(self, run):
        run.return_value = self.completed(
            stdout='{"ok": true, "data": {"name": "work"}}\n'
        )
        self.backend.new("work")
        self.assertEqual(
            run.call_args.args[0][-3:],
            ["--json", "new", "work"],
        )

    @mock.patch.object(tmux_tui.subprocess, "run")
    def test_error_envelope_becomes_backend_error(self, run):
        run.return_value = self.completed(
            returncode=4,
            stderr=json.dumps({
                "ok": False,
                "error": "session already exists",
                "code": "EEXIST",
                "exit": 4,
            }),
        )
        with self.assertRaises(tmux_tui.BackendError) as caught:
            self.backend.new("work")
        self.assertEqual(caught.exception.code, "EEXIST")
        self.assertEqual(caught.exception.exit_code, 4)

    @mock.patch.object(tmux_tui.subprocess, "run")
    def test_attach_inherits_terminal_and_keeps_exact_pane_target(self, run):
        run.return_value = self.completed(returncode=0)
        self.assertEqual(self.backend.attach("work:2.1"), 0)
        run.assert_called_once_with([
            "python3", "/opt/tmux-cli/tb.py", "attach", "work:2.1",
        ])


class DiscoveryTests(unittest.TestCase):
    def test_explicit_python_entrypoint(self):
        command = tmux_tui.discover_tb(str(ROOT.parent / "tmux-cli" / "tb.py"))
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[1].endswith("/tmux-cli/tb.py"))

    def test_missing_explicit_can_fall_back_to_sibling_checkout(self):
        command = tmux_tui.discover_tb("/definitely/missing/tb")
        self.assertTrue(command[-1].endswith("/tmux-cli/tb.py"))


if __name__ == "__main__":
    unittest.main()
