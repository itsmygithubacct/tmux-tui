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

    def test_pane_identity_prefers_stable_tmux_id(self):
        pane = {
            "session": "build",
            "window": "3",
            "pane": "2",
            "pane_id": "%17",
        }
        self.assertEqual(tmux_tui.pane_identity(pane), "%17")
        pane.pop("pane_id")
        self.assertEqual(tmux_tui.pane_identity(pane), "build:3.2")

    def test_preview_removes_terminal_controls(self):
        lines = tmux_tui.clean_preview("\x1b[31mred\x1b[0m\tok\nbad\x01")
        self.assertEqual(lines, ["red ok", "bad�"])

    def test_inline_metadata_cannot_inject_terminal_controls(self):
        self.assertEqual(
            tmux_tui.clean_inline("\x1b[31mname\x1b[0m\nnext"),
            "name�next",
        )
        self.assertEqual(tmux_tui.clip("\x1b[31mwide界\x1b[0m", 5), "wide…")

    def test_clip_uses_terminal_cells_for_wide_and_combining_text(self):
        self.assertEqual(tmux_tui.display_width("a界e\u0301"), 4)
        self.assertEqual(tmux_tui.clip("a界b", 3), "a…")
        self.assertEqual(tmux_tui.fit("界", 4), "界  ")

    def test_prompt_view_keeps_wide_character_cursor_inside_field(self):
        visible, cursor_column = tmux_tui.input_view(
            list("ab界cd"), 5, 4,
        )
        self.assertLessEqual(tmux_tui.display_width(visible), 4)
        self.assertLess(cursor_column, 4)
        self.assertTrue(visible.endswith("cd"))

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
            [
                "python3",
                "/opt/tmux-cli/tb.py",
                "--json",
                "snapshot",
                "--tmux-only",
            ],
        )
        self.assertEqual(run.call_args.kwargs["env"]["NO_COLOR"], "1")
        self.assertEqual(run.call_args.kwargs["errors"], "replace")

    @mock.patch.object(tmux_tui.subprocess, "run")
    def test_snapshot_falls_back_once_for_older_tmux_cli(self, run):
        unsupported = self.completed(
            returncode=2,
            stderr=json.dumps({
                "ok": False,
                "error": "unrecognized arguments: --tmux-only",
                "code": "EUSAGE",
                "exit": 2,
            }),
        )
        success = self.completed(stdout=json.dumps({
            "ok": True,
            "data": {"sessions": [], "panes": [], "tmux_server": False},
        }))
        run.side_effect = [unsupported, success, success]

        self.backend.snapshot()
        self.backend.snapshot()

        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(calls[0][-2:], ["snapshot", "--tmux-only"])
        self.assertEqual(calls[1][-1:], ["snapshot"])
        self.assertEqual(calls[2][-1:], ["snapshot"])

    @mock.patch.object(tmux_tui.subprocess, "run")
    def test_snapshot_can_bundle_selected_pane_preview(self, run):
        run.return_value = self.completed(stdout=json.dumps({
            "ok": True,
            "data": {
                "sessions": [],
                "panes": [],
                "tmux_server": True,
                "capture": {
                    "target": "work:2.1",
                    "lines": 25,
                    "content": "ready\n",
                    "error": "",
                },
            },
        }))

        data = self.backend.snapshot(
            capture_target="work:2.1",
            capture_lines=25,
        )

        self.assertEqual(data["capture"]["content"], "ready\n")
        self.assertEqual(
            run.call_args.args[0][-5:],
            [
                "snapshot", "--tmux-only", "--capture=work:2.1",
                "--lines", "25",
            ],
        )

    def test_malformed_error_exit_still_becomes_backend_error(self):
        proc = self.completed(
            returncode=7,
            stderr=json.dumps({
                "ok": False,
                "error": "tmux failed",
                "code": "ETMUX",
                "exit": "not-an-integer",
            }),
        )
        error = self.backend._decode_error(proc)
        self.assertEqual(error.code, "ETMUX")
        self.assertEqual(error.exit_code, 7)

    @mock.patch.object(tmux_tui.subprocess, "run")
    def test_mutations_go_through_tb(self, run):
        run.return_value = self.completed(
            stdout='{"ok": true, "data": {"name": "work"}}\n'
        )
        self.backend.new("work")
        self.assertEqual(
            run.call_args.args[0][-3:],
            ["new", "--", "work"],
        )

    @mock.patch.object(tmux_tui.subprocess, "run")
    def test_type_uses_option_boundary_for_dash_prefixed_text(self, run):
        run.return_value = self.completed(
            stdout='{"ok": true, "data": {"typed": "-n"}}\n'
        )

        self.backend.type_line("work:0.0", "-n")

        self.assertEqual(
            run.call_args.args[0][-4:],
            ["type", "--", "work:0.0", "-n"],
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
            "python3", "/opt/tmux-cli/tb.py", "attach", "--", "work:2.1",
        ])


class DiscoveryTests(unittest.TestCase):
    def test_explicit_python_entrypoint(self):
        command = tmux_tui.discover_tb(str(ROOT.parent / "tmux-cli" / "tb.py"))
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[1].endswith("/tmux-cli/tb.py"))

    def test_missing_explicit_fails_closed(self):
        with self.assertRaises(tmux_tui.BackendError) as caught:
            tmux_tui.discover_tb("/definitely/missing/tb")
        self.assertEqual(caught.exception.code, "ENOCLI")
        self.assertIn("--tb", caught.exception.message)

    def test_missing_configured_backend_fails_closed(self):
        with mock.patch.dict(
            tmux_tui.os.environ,
            {"TMUX_CLI": "/definitely/missing/tb"},
        ):
            with self.assertRaises(tmux_tui.BackendError) as caught:
                tmux_tui.discover_tb()
        self.assertIn("TMUX_CLI", caught.exception.message)


class CommandLineTests(unittest.TestCase):
    def test_refresh_rejects_non_finite_values(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value):
                with self.assertRaises(SystemExit) as caught:
                    tmux_tui.main([f"--refresh={value}"])
                self.assertIn("finite positive", str(caught.exception))


class FakeBackend:
    def __init__(self, *snapshots):
        self.snapshots = list(snapshots)

    def snapshot(self, **_kwargs):
        value = self.snapshots.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def capture(self, _target, _lines):
        return ""


def snapshot(*panes):
    names = sorted({str(pane["session"]) for pane in panes})
    return {
        "host": "test",
        "tmux_server": bool(names),
        "sessions": [
            {
                "name": name,
                "windows": 1,
                "attached": 0,
                "activity": 0,
            }
            for name in names
        ],
        "panes": list(panes),
    }


class ManagerStateTests(unittest.TestCase):
    def manager(self, backend):
        return tmux_tui.TmuxManager(
            mock.Mock(),
            backend,
            preview_enabled=False,
        )

    def test_failed_refresh_waits_for_the_normal_retry_interval(self):
        manager = self.manager(FakeBackend(
            tmux_tui.BackendError("backend unavailable"),
        ))
        manager.force_refresh = True

        manager.refresh()

        self.assertFalse(manager.force_refresh)
        self.assertEqual(manager.message, "backend unavailable")
        self.assertEqual(manager.message_kind, "error")

    def test_refresh_preserves_selected_pane_by_stable_id(self):
        manager = self.manager(FakeBackend(
            snapshot(
                {
                    "session": "work", "window": "0", "pane": "0",
                    "pane_id": "%1",
                },
                {
                    "session": "work", "window": "0", "pane": "1",
                    "pane_id": "%2",
                },
            ),
            snapshot(
                {
                    "session": "work", "window": "0", "pane": "0",
                    "pane_id": "%2",
                },
                {
                    "session": "work", "window": "0", "pane": "1",
                    "pane_id": "%1",
                },
            ),
        ))
        manager.refresh()
        manager.pane_indices["work"] = 1

        manager.refresh()

        pane = manager.current_pane()
        self.assertIsNotNone(pane)
        self.assertEqual(tmux_tui.pane_identity(pane or {}), "%2")
        self.assertEqual(manager.pane_indices["work"], 0)

    def test_explicit_post_action_session_selection_wins_on_refresh(self):
        manager = self.manager(FakeBackend(
            snapshot({
                "session": "alpha", "window": "0", "pane": "0",
            }),
            snapshot(
                {"session": "alpha", "window": "0", "pane": "0"},
                {"session": "zulu", "window": "0", "pane": "0"},
            ),
        ))
        manager.refresh()
        manager.query = "alpha"
        manager.select_after_refresh("zulu")

        manager.refresh()

        self.assertEqual(manager.current_session_name(), "zulu")
        self.assertEqual(manager.query, "")

    def test_filter_matches_pane_command_window_and_cwd(self):
        manager = self.manager(FakeBackend(snapshot(
            {
                "session": "work", "window": "0", "pane": "0",
                "window_name": "api", "command": "python",
                "cwd": "/srv/project",
            },
            {
                "session": "notes", "window": "0", "pane": "0",
                "window_name": "editor", "command": "vim",
                "cwd": "/tmp",
            },
        )))
        manager.refresh()

        for query in ("python", "api", "/srv/project"):
            manager.query = query
            manager.apply_filter()
            self.assertEqual(
                [row["name"] for row in manager.filtered_sessions],
                ["work"],
            )

    def test_prefetched_preview_avoids_a_second_backend_process(self):
        backend = mock.Mock()
        manager = tmux_tui.TmuxManager(mock.Mock(), backend)
        manager.sessions = [{"name": "work"}]
        manager.filtered_sessions = list(manager.sessions)
        manager.panes = {
            "work": [{
                "session": "work",
                "window": "0",
                "pane": "0",
                "pane_id": "%1",
            }],
        }

        manager._refresh_preview({
            "target": "work:0.0",
            "content": "\x1b[31mready\x1b[0m\n",
            "error": "",
        })

        self.assertEqual(manager.preview, ["ready"])
        backend.capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
