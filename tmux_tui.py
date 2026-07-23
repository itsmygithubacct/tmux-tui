#!/usr/bin/env python3
"""Tmux Manager — a friendly terminal UI powered exclusively by tmux-cli."""

from __future__ import annotations

import argparse
import curses
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Sequence


VERSION = "0.1.0"
DEFAULT_REFRESH = 1.0
DEFAULT_PREVIEW_LINES = 80
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
ANSI_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[()][0-2A-Z])"
)


class BackendError(RuntimeError):
    """A stable tmux-cli failure presented to the UI."""

    def __init__(self, message: str, *, code: str = "EUNKNOWN", exit_code: int = 1):
        super().__init__(message)
        self.message = message
        self.code = code
        self.exit_code = exit_code


def discover_tb(explicit: str | None = None) -> list[str]:
    """Return the command prefix for the selected tmux-cli entrypoint."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if configured := os.environ.get("TMUX_CLI"):
        candidates.append(Path(configured).expanduser())

    here = Path(__file__).resolve().parent
    candidates.extend((
        here / "tmux-cli" / "tb.py",
        here.parent / "tmux-cli" / "tb.py",  # sibling developer checkout
    ))

    for candidate in candidates:
        if candidate.is_dir():
            candidate = candidate / "tb.py"
        if candidate.is_file():
            if candidate.suffix == ".py" or not os.access(candidate, os.X_OK):
                return [sys.executable, str(candidate)]
            return [str(candidate)]

    installed = shutil.which("tb")
    if installed:
        return [installed]
    raise BackendError(
        "tmux-cli was not found; initialize the tmux-cli submodule or pass --tb",
        code="ENOCLI",
    )


class TmuxCLI:
    """Small typed adapter around tmux-cli's stable JSON envelope."""

    def __init__(self, command: Sequence[str]):
        self.command = list(command)

    @classmethod
    def discover(cls, explicit: str | None = None) -> "TmuxCLI":
        return cls(discover_tb(explicit))

    @staticmethod
    def _decode_error(proc: subprocess.CompletedProcess[str]) -> BackendError:
        text = (proc.stderr or proc.stdout or "").strip()
        try:
            envelope = json.loads(text)
        except (TypeError, ValueError):
            return BackendError(
                text or f"tmux-cli exited with status {proc.returncode}",
                exit_code=proc.returncode or 1,
            )
        if isinstance(envelope, dict):
            return BackendError(
                str(envelope.get("error") or "tmux-cli command failed"),
                code=str(envelope.get("code") or "EUNKNOWN"),
                exit_code=int(envelope.get("exit") or proc.returncode or 1),
            )
        return BackendError(
            text or "tmux-cli returned an invalid error",
            exit_code=proc.returncode or 1,
        )

    def json(self, *args: str, timeout: float = 8.0) -> Any:
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        try:
            proc = subprocess.run(
                [*self.command, "--json", *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except FileNotFoundError as error:
            raise BackendError(f"could not run tmux-cli: {error}", code="ENOCLI") from error
        except subprocess.TimeoutExpired as error:
            raise BackendError("tmux-cli did not respond", code="ETIMEDOUT", exit_code=5) from error
        if proc.returncode:
            raise self._decode_error(proc)
        try:
            envelope = json.loads(proc.stdout)
        except (TypeError, ValueError) as error:
            raise BackendError("tmux-cli returned invalid JSON", code="EPROTOCOL") from error
        if not isinstance(envelope, dict) or envelope.get("ok") is not True:
            raise BackendError("tmux-cli returned an invalid success envelope", code="EPROTOCOL")
        return envelope.get("data")

    def snapshot(self) -> dict[str, Any]:
        data = self.json("snapshot")
        if not isinstance(data, dict):
            raise BackendError("snapshot payload is not an object", code="EPROTOCOL")
        return data

    def capture(self, target: str, lines: int) -> str:
        data = self.json("capture", target, "--lines", str(lines))
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            raise BackendError("capture payload is invalid", code="EPROTOCOL")
        return data["content"]

    def new(self, name: str) -> None:
        self.json("new", name)

    def rename(self, old: str, new: str) -> None:
        self.json("rename", old, new)

    def kill(self, target: str) -> None:
        self.json("kill", target, "--force")

    def type_line(self, target: str, text: str) -> None:
        self.json("type", target, text)

    def attach(self, target: str) -> int:
        """Attach in a child so detaching returns to the manager."""
        try:
            return subprocess.run([*self.command, "attach", target]).returncode
        except FileNotFoundError as error:
            raise BackendError(f"could not run tmux-cli: {error}", code="ENOCLI") from error


def as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def pane_target(pane: dict[str, Any]) -> str:
    return f"{pane.get('session', '')}:{pane.get('window', '0')}.{pane.get('pane', '0')}"


def sort_panes(panes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        panes,
        key=lambda pane: (
            str(pane.get("session", "")).lower(),
            as_int(pane.get("window")),
            as_int(pane.get("pane")),
        ),
    )


def group_panes(panes: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for pane in sort_panes(panes):
        grouped.setdefault(str(pane.get("session", "")), []).append(pane)
    return grouped


def format_age(seconds: int) -> str:
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def clean_preview(text: str) -> list[str]:
    text = ANSI_RE.sub("", text)
    return [CONTROL_RE.sub("�", line.expandtabs(4)) for line in text.splitlines()]


def clip(text: object, width: int) -> str:
    value = str(text)
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width == 1:
        return "…"
    return value[: width - 1] + "…"


@dataclass
class Palette:
    normal: int = curses.A_NORMAL
    dim: int = curses.A_DIM
    title: int = curses.A_BOLD
    header: int = curses.A_REVERSE | curses.A_BOLD
    selected: int = curses.A_REVERSE | curses.A_BOLD
    accent: int = curses.A_BOLD
    good: int = curses.A_BOLD
    warn: int = curses.A_BOLD
    error: int = curses.A_BOLD


class TmuxManager:
    def __init__(
        self,
        stdscr: Any,
        backend: TmuxCLI,
        *,
        refresh_interval: float = DEFAULT_REFRESH,
        preview_lines: int = DEFAULT_PREVIEW_LINES,
        initial_session: str | None = None,
        preview_enabled: bool = True,
    ):
        self.screen = stdscr
        self.backend = backend
        self.refresh_interval = max(0.2, refresh_interval)
        self.preview_lines = max(10, preview_lines)
        self.preview_enabled = preview_enabled
        self.initial_session = initial_session

        self.sessions: list[dict[str, Any]] = []
        self.filtered_sessions: list[dict[str, Any]] = []
        self.panes: dict[str, list[dict[str, Any]]] = {}
        self.session_index = 0
        self.pane_indices: dict[str, int] = {}
        self.focus = "sessions"
        self.query = ""
        self.preview: list[str] = []
        self.preview_target = ""
        self.host = ""
        self.server_running = False
        self.message = "Loading tmux sessions…"
        self.message_kind = "normal"
        self.message_until = 0.0
        self.last_refresh = 0.0
        self.force_refresh = True
        self.palette = Palette()
        self.list_top = 0
        self.pane_top = 0
        self._layout: dict[str, tuple[int, int, int, int]] = {}

    def setup(self) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.screen.keypad(True)
        self.screen.timeout(100)
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        if curses.has_colors():
            curses.start_color()
            pairs = (
                (1, curses.COLOR_CYAN, -1),
                (2, curses.COLOR_BLACK, curses.COLOR_CYAN),
                (3, curses.COLOR_GREEN, -1),
                (4, curses.COLOR_YELLOW, -1),
                (5, curses.COLOR_RED, -1),
                (6, curses.COLOR_BLUE, -1),
            )
            for number, fg, bg in pairs:
                try:
                    curses.init_pair(number, fg, bg)
                except curses.error:
                    pass
            self.palette = Palette(
                normal=curses.A_NORMAL,
                dim=curses.A_DIM,
                title=curses.color_pair(1) | curses.A_BOLD,
                header=curses.color_pair(2) | curses.A_BOLD,
                selected=curses.color_pair(2) | curses.A_BOLD,
                accent=curses.color_pair(1) | curses.A_BOLD,
                good=curses.color_pair(3) | curses.A_BOLD,
                warn=curses.color_pair(4) | curses.A_BOLD,
                error=curses.color_pair(5) | curses.A_BOLD,
            )
        try:
            curses.mousemask(curses.BUTTON1_CLICKED)
            curses.mouseinterval(0)
        except curses.error:
            pass

    def current_session(self) -> dict[str, Any] | None:
        if not self.filtered_sessions:
            return None
        self.session_index = max(0, min(self.session_index, len(self.filtered_sessions) - 1))
        return self.filtered_sessions[self.session_index]

    def current_session_name(self) -> str | None:
        session = self.current_session()
        return str(session.get("name")) if session else None

    def current_panes(self) -> list[dict[str, Any]]:
        name = self.current_session_name()
        return self.panes.get(name or "", [])

    def current_pane(self) -> dict[str, Any] | None:
        name = self.current_session_name()
        panes = self.current_panes()
        if not name or not panes:
            return None
        index = max(0, min(self.pane_indices.get(name, 0), len(panes) - 1))
        self.pane_indices[name] = index
        return panes[index]

    def current_target(self) -> str | None:
        session = self.current_session_name()
        if not session:
            return None
        pane = self.current_pane()
        return pane_target(pane) if pane else session

    def apply_filter(self, *, preserve_name: str | None = None) -> None:
        query = self.query.casefold()
        self.filtered_sessions = [
            session for session in self.sessions
            if query in str(session.get("name", "")).casefold()
        ]
        if preserve_name:
            for index, session in enumerate(self.filtered_sessions):
                if session.get("name") == preserve_name:
                    self.session_index = index
                    break
            else:
                self.session_index = min(
                    self.session_index, max(0, len(self.filtered_sessions) - 1)
                )
        else:
            self.session_index = min(
                self.session_index, max(0, len(self.filtered_sessions) - 1)
            )

    def set_message(self, message: str, kind: str = "normal", seconds: float = 4.0) -> None:
        self.message = message
        self.message_kind = kind
        self.message_until = time.monotonic() + seconds

    def refresh(self) -> None:
        selected = self.current_session_name() or self.initial_session
        try:
            data = self.backend.snapshot()
        except BackendError as error:
            self.server_running = False
            self.set_message(error.message, "error", 6.0)
            self.last_refresh = time.monotonic()
            return

        raw_sessions = data.get("sessions", [])
        raw_panes = data.get("panes", [])
        self.sessions = (
            sorted(
                (row for row in raw_sessions if isinstance(row, dict)),
                key=lambda row: str(row.get("name", "")).casefold(),
            )
            if isinstance(raw_sessions, list) else []
        )
        self.panes = group_panes(
            [row for row in raw_panes if isinstance(row, dict)]
            if isinstance(raw_panes, list) else []
        )
        self.host = str(data.get("host") or "")
        self.server_running = bool(data.get("tmux_server"))
        self.apply_filter(preserve_name=selected)
        self.initial_session = None

        live_names = {str(row.get("name")) for row in self.sessions}
        self.pane_indices = {
            name: index for name, index in self.pane_indices.items()
            if name in live_names
        }
        for name, panes in self.panes.items():
            self.pane_indices[name] = min(
                self.pane_indices.get(name, 0), max(0, len(panes) - 1)
            )
        self._refresh_preview()
        if self.message_until < time.monotonic():
            count = len(self.sessions)
            self.message = f"{count} session{'s' if count != 1 else ''}"
            self.message_kind = "good" if count else "normal"
        self.last_refresh = time.monotonic()
        self.force_refresh = False

    def _refresh_preview(self) -> None:
        target = self.current_target()
        if not self.preview_enabled or not target:
            self.preview = []
            self.preview_target = target or ""
            return
        try:
            self.preview = clean_preview(
                self.backend.capture(target, self.preview_lines)
            )
            self.preview_target = target
        except BackendError as error:
            self.preview = [f"[preview unavailable: {error.message}]"]
            self.preview_target = target

    def add(self, y: int, x: int, text: object, width: int, attr: int = 0) -> None:
        height, screen_width = self.screen.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= screen_width or width <= 0:
            return
        try:
            self.screen.addnstr(y, x, str(text), min(width, screen_width - x), attr)
        except curses.error:
            pass

    def fill(self, y: int, x: int, width: int, char: str = " ", attr: int = 0) -> None:
        self.add(y, x, char * max(0, width), width, attr)

    def hline(self, y: int, x: int, width: int) -> None:
        if width <= 0:
            return
        try:
            self.screen.hline(y, x, curses.ACS_HLINE, width)
        except curses.error:
            self.add(y, x, "-" * width, width, self.palette.dim)

    def vline(self, y: int, x: int, height: int) -> None:
        if height <= 0:
            return
        try:
            self.screen.vline(y, x, curses.ACS_VLINE, height)
        except curses.error:
            for row in range(y, y + height):
                self.add(row, x, "|", 1, self.palette.dim)

    def box(self, y: int, x: int, height: int, width: int, title: str, active: bool) -> None:
        if height < 2 or width < 2:
            return
        self.hline(y, x + 1, width - 2)
        self.hline(y + height - 1, x + 1, width - 2)
        self.vline(y + 1, x, height - 2)
        self.vline(y + 1, x + width - 1, height - 2)
        for row, col, glyph in (
            (y, x, curses.ACS_ULCORNER),
            (y, x + width - 1, curses.ACS_URCORNER),
            (y + height - 1, x, curses.ACS_LLCORNER),
            (y + height - 1, x + width - 1, curses.ACS_LRCORNER),
        ):
            try:
                self.screen.addch(row, col, glyph)
            except curses.error:
                pass
        label = f" {title} "
        self.add(
            y, x + 2, label, max(0, width - 4),
            self.palette.accent if active else self.palette.title,
        )

    def draw(self) -> None:
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if height < 14 or width < 56:
            self.add(0, 0, " Tmux Manager ", width, self.palette.header)
            self.add(2, 2, "Terminal is too small.", width - 4, self.palette.error)
            self.add(4, 2, "Minimum: 56 columns × 14 rows", width - 4)
            self.add(height - 1, 1, "q quit", max(0, width - 2), self.palette.dim)
            self.screen.refresh()
            return

        self.fill(0, 0, width, " ", self.palette.header)
        self.add(0, 2, "Tmux Manager", width - 4, self.palette.header)
        server = "online" if self.server_running else "idle"
        summary = f"{self.host or 'local'} · tmux {server}"
        self.add(0, max(2, width - len(summary) - 2), summary, len(summary), self.palette.header)

        filter_text = f"filter: {self.query}" if self.query else "All sessions"
        self.add(1, 2, filter_text, width - 4, self.palette.dim)

        top = 2
        bottom = height - 3
        content_h = bottom - top + 1
        left_w = max(25, min(38, width // 3))
        gap = 1
        right_x = left_w + gap
        right_w = width - right_x
        self._layout = {
            "sessions": (top, 0, content_h, left_w),
            "detail": (top, right_x, content_h, right_w),
        }
        self._draw_sessions(top, 0, content_h, left_w)
        self._draw_detail(top, right_x, content_h, right_w)

        self.hline(height - 2, 0, width)
        keys = "Enter attach  n new  e rename  d kill  s send  / filter  ? help  q quit"
        self.add(height - 1, 1, keys, width - 2, self.palette.dim)
        message_attr = getattr(self.palette, self.message_kind, self.palette.normal)
        self.add(height - 2, 2, clip(self.message, width - 4), width - 4, message_attr)
        self.screen.refresh()

    def _draw_sessions(self, y: int, x: int, height: int, width: int) -> None:
        self.box(y, x, height, width, "Sessions", self.focus == "sessions")
        inside_h = height - 2
        if not self.filtered_sessions:
            label = "No matches" if self.query else "No tmux sessions"
            self.add(y + 2, x + 2, label, width - 4, self.palette.dim)
            self.add(y + 4, x + 2, "Press n to create one", width - 4, self.palette.accent)
            return

        visible = max(1, inside_h - 1)
        if self.session_index < self.list_top:
            self.list_top = self.session_index
        if self.session_index >= self.list_top + visible:
            self.list_top = self.session_index - visible + 1
        self.list_top = max(
            0, min(self.list_top, max(0, len(self.filtered_sessions) - visible))
        )
        now = int(time.time())
        for offset, session in enumerate(
            self.filtered_sessions[self.list_top:self.list_top + visible]
        ):
            index = self.list_top + offset
            row_y = y + 1 + offset
            name = str(session.get("name", ""))
            attached = as_int(session.get("attached"))
            windows = as_int(session.get("windows"))
            idle = format_age(now - as_int(session.get("activity"), now))
            badge = f"{windows}w"
            if attached:
                badge += f" {attached}a"
            badge += f" {idle}"
            available_name = max(1, width - len(badge) - 5)
            line = f" {clip(name, available_name).ljust(available_name)} {badge}"
            attr = (
                self.palette.selected
                if index == self.session_index
                else (self.palette.good if attached else self.palette.normal)
            )
            self.fill(row_y, x + 1, width - 2, " ", attr)
            self.add(row_y, x + 1, line, width - 2, attr)

    def _draw_detail(self, y: int, x: int, height: int, width: int) -> None:
        session = self.current_session()
        title = str(session.get("name")) if session else "Details"
        self.box(y, x, height, width, title, self.focus == "panes")
        if not session:
            self.add(y + 2, x + 2, "Select or create a session.", width - 4, self.palette.dim)
            return

        panes = self.current_panes()
        pane_h = min(max(4, len(panes) + 2), max(4, height // 3))
        divider_y = y + pane_h
        self.hline(divider_y, x + 1, width - 2)
        pane_title = f" Panes ({len(panes)}) "
        self.add(divider_y, x + 2, pane_title, width - 4, self.palette.title)

        pane_visible = max(1, pane_h - 1)
        pane_index = self.pane_indices.get(str(session.get("name")), 0)
        if pane_index < self.pane_top:
            self.pane_top = pane_index
        if pane_index >= self.pane_top + pane_visible:
            self.pane_top = pane_index - pane_visible + 1
        self.pane_top = max(0, min(self.pane_top, max(0, len(panes) - pane_visible)))
        for offset, pane in enumerate(panes[self.pane_top:self.pane_top + pane_visible]):
            index = self.pane_top + offset
            row_y = y + 1 + offset
            marker = "●" if pane.get("active") else " "
            target = f"{pane.get('window', '0')}.{pane.get('pane', '0')}"
            command = str(pane.get("command") or "?")
            window_name = str(pane.get("window_name") or "")
            cwd = str(pane.get("cwd") or "")
            prefix = f" {marker} {target:<5} {clip(window_name, 12):<12} {clip(command, 12):<12} "
            rest = max(0, width - len(prefix) - 2)
            line = prefix + clip(cwd, rest)
            selected = index == pane_index
            attr = (
                self.palette.selected
                if selected and self.focus == "panes"
                else (self.palette.accent if selected else self.palette.normal)
            )
            self.fill(row_y, x + 1, width - 2, " ", attr)
            self.add(row_y, x + 1, line, width - 2, attr)

        preview_y = divider_y + 1
        preview_h = y + height - 1 - preview_y
        preview_label = f" Preview · {self.preview_target or title} "
        self.add(divider_y, max(x + 2, x + width - len(preview_label) - 2),
                 preview_label, width - 4, self.palette.dim)
        if not self.preview_enabled:
            self.add(preview_y + 1, x + 2, "Preview disabled", width - 4, self.palette.dim)
            return
        rows = self.preview[-max(0, preview_h):]
        for offset, line in enumerate(rows):
            self.add(preview_y + offset, x + 2, line, width - 4, self.palette.dim)

    def move(self, delta: int) -> None:
        if self.focus == "panes":
            name = self.current_session_name()
            panes = self.current_panes()
            if name and panes:
                self.pane_indices[name] = (
                    self.pane_indices.get(name, 0) + delta
                ) % len(panes)
                self._refresh_preview()
            return
        if self.filtered_sessions:
            self.session_index = (
                self.session_index + delta
            ) % len(self.filtered_sessions)
            self.pane_top = 0
            self._refresh_preview()

    def jump(self, end: bool) -> None:
        if self.focus == "panes":
            name = self.current_session_name()
            panes = self.current_panes()
            if name and panes:
                self.pane_indices[name] = len(panes) - 1 if end else 0
                self._refresh_preview()
        elif self.filtered_sessions:
            self.session_index = len(self.filtered_sessions) - 1 if end else 0
            self._refresh_preview()

    def prompt(self, title: str, initial: str = "", *, allow_empty: bool = False) -> str | None:
        height, width = self.screen.getmaxyx()
        box_w = min(max(42, len(title) + 8), width - 4)
        box_h = 5
        y = max(1, (height - box_h) // 2)
        x = max(1, (width - box_w) // 2)
        try:
            window = curses.newwin(box_h, box_w, y, x)
            window.keypad(True)
            window.box()
            window.addnstr(0, 2, f" {title} ", box_w - 4, self.palette.title)
            window.addnstr(3, 2, "Enter accept · Esc cancel", box_w - 4, self.palette.dim)
            value = list(initial)
            cursor = len(value)
            curses.curs_set(1)
            while True:
                display = "".join(value)
                window.addstr(2, 2, " " * (box_w - 4))
                start = max(0, cursor - (box_w - 6))
                visible = display[start:start + box_w - 4]
                window.addnstr(2, 2, visible, box_w - 4)
                window.move(2, 2 + min(cursor - start, box_w - 5))
                window.refresh()
                key = window.get_wch()
                if key in ("\n", "\r") or key == curses.KEY_ENTER:
                    result = "".join(value).strip()
                    if result or allow_empty:
                        return result
                    curses.beep()
                elif key == "\x1b":
                    return None
                elif key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
                    if cursor:
                        cursor -= 1
                        value.pop(cursor)
                elif key == curses.KEY_DC:
                    if cursor < len(value):
                        value.pop(cursor)
                elif key == curses.KEY_LEFT:
                    cursor = max(0, cursor - 1)
                elif key == curses.KEY_RIGHT:
                    cursor = min(len(value), cursor + 1)
                elif key == curses.KEY_HOME:
                    cursor = 0
                elif key == curses.KEY_END:
                    cursor = len(value)
                elif isinstance(key, str) and key.isprintable():
                    value.insert(cursor, key)
                    cursor += 1
        except curses.error:
            return None
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.screen.touchwin()

    def confirm(self, title: str, question: str) -> bool:
        height, width = self.screen.getmaxyx()
        box_w = min(max(44, len(question) + 6), width - 4)
        y = max(1, height // 2 - 2)
        x = max(1, (width - box_w) // 2)
        try:
            window = curses.newwin(5, box_w, y, x)
            window.keypad(True)
            window.box()
            window.addnstr(0, 2, f" {title} ", box_w - 4, self.palette.warn)
            window.addnstr(2, 2, question, box_w - 4)
            window.addnstr(3, 2, "y confirm · any other key cancel", box_w - 4, self.palette.dim)
            window.refresh()
            key = window.get_wch()
            return key in ("y", "Y")
        except curses.error:
            return False
        finally:
            self.screen.touchwin()

    def show_help(self) -> None:
        lines = [
            ("Navigation", self.palette.title),
            ("  ↑/↓ or j/k    move through sessions or panes", self.palette.normal),
            ("  ←/→ or Tab    switch between session and pane lists", self.palette.normal),
            ("  Home/End      jump to first or last item", self.palette.normal),
            ("", self.palette.normal),
            ("Actions", self.palette.title),
            ("  Enter/o       attach to the selected pane; detach returns here", self.palette.normal),
            ("  n             create a session", self.palette.normal),
            ("  e             rename the selected session", self.palette.normal),
            ("  d             kill the selected session (confirmation required)", self.palette.normal),
            ("  s             type a command into the selected pane", self.palette.normal),
            ("  /             filter sessions by name", self.palette.normal),
            ("  r             refresh now", self.palette.normal),
            ("  p             toggle the live preview", self.palette.normal),
            ("  q             quit", self.palette.normal),
            ("", self.palette.normal),
            ("Every operation above is performed through tmux-cli (`tb`).", self.palette.dim),
        ]
        height, width = self.screen.getmaxyx()
        box_h = min(height - 2, len(lines) + 4)
        box_w = min(width - 2, 70)
        y, x = max(0, (height - box_h) // 2), max(0, (width - box_w) // 2)
        try:
            window = curses.newwin(box_h, box_w, y, x)
            window.box()
            window.addnstr(0, 2, " Help ", box_w - 4, self.palette.title)
            for index, (line, attr) in enumerate(lines[:box_h - 3], 1):
                window.addnstr(index, 2, line, box_w - 4, attr)
            window.addnstr(box_h - 2, 2, "Press any key", box_w - 4, self.palette.dim)
            window.refresh()
            window.get_wch()
        except curses.error:
            pass
        self.screen.touchwin()

    def create_session(self) -> None:
        name = self.prompt("New session name")
        if name is None:
            return
        try:
            self.backend.new(name)
        except BackendError as error:
            self.set_message(error.message, "error", 6.0)
            return
        self.initial_session = name
        self.set_message(f"Created {name}", "good")
        self.force_refresh = True

    def rename_session(self) -> None:
        old = self.current_session_name()
        if not old:
            self.set_message("There is no session to rename.", "warn")
            return
        new = self.prompt("Rename session", old)
        if new is None or new == old:
            return
        try:
            self.backend.rename(old, new)
        except BackendError as error:
            self.set_message(error.message, "error", 6.0)
            return
        self.initial_session = new
        self.set_message(f"Renamed {old} → {new}", "good")
        self.force_refresh = True

    def kill_session(self) -> None:
        name = self.current_session_name()
        if not name:
            self.set_message("There is no session to kill.", "warn")
            return
        if not self.confirm("Kill session", f"Kill {name!r} and its processes?"):
            self.set_message("Kill cancelled.")
            return
        try:
            self.backend.kill(name)
        except BackendError as error:
            self.set_message(error.message, "error", 6.0)
            return
        self.set_message(f"Killed {name}", "good")
        self.force_refresh = True

    def send_line(self) -> None:
        target = self.current_target()
        if not target:
            self.set_message("There is no pane to send to.", "warn")
            return
        text = self.prompt(f"Type into {target}", allow_empty=False)
        if text is None:
            return
        try:
            self.backend.type_line(target, text)
        except BackendError as error:
            self.set_message(error.message, "error", 6.0)
            return
        self.set_message(f"Sent command to {target}", "good")
        self.force_refresh = True

    def edit_filter(self) -> None:
        query = self.prompt("Filter sessions", self.query, allow_empty=True)
        if query is None:
            return
        self.query = query
        self.session_index = 0
        self.list_top = 0
        self.apply_filter()
        self._refresh_preview()
        self.set_message(
            f"Filter: {query}" if query else "Filter cleared",
            "normal",
        )

    def attach(self) -> None:
        target = self.current_target()
        if not target:
            self.set_message("There is no session to attach.", "warn")
            return
        try:
            curses.def_prog_mode()
            curses.endwin()
            rc = self.backend.attach(target)
        except BackendError as error:
            rc = error.exit_code
            self.set_message(error.message, "error", 6.0)
        finally:
            try:
                curses.reset_prog_mode()
                self.screen.refresh()
                curses.curs_set(0)
                curses.flushinp()
            except curses.error:
                pass
        if rc:
            self.set_message(f"Attach exited with status {rc}", "error", 6.0)
        else:
            self.set_message(f"Detached from {target}", "good")
        self.force_refresh = True

    def handle_mouse(self) -> None:
        try:
            _mouse_id, mx, my, _z, state = curses.getmouse()
        except curses.error:
            return
        if not state & curses.BUTTON1_CLICKED:
            return
        sessions_rect = self._layout.get("sessions")
        detail_rect = self._layout.get("detail")
        if sessions_rect:
            y, x, height, width = sessions_rect
            if x < mx < x + width - 1 and y < my < y + height - 1:
                index = self.list_top + (my - y - 1)
                if 0 <= index < len(self.filtered_sessions):
                    self.session_index = index
                    self.focus = "sessions"
                    self.pane_top = 0
                    self._refresh_preview()
                    return
        if detail_rect:
            y, x, height, width = detail_rect
            panes = self.current_panes()
            pane_h = min(max(4, len(panes) + 2), max(4, height // 3))
            if x < mx < x + width - 1 and y < my < y + pane_h:
                index = self.pane_top + (my - y - 1)
                name = self.current_session_name()
                if name and 0 <= index < len(panes):
                    self.pane_indices[name] = index
                    self.focus = "panes"
                    self._refresh_preview()

    def handle_key(self, key: object) -> bool:
        if key in ("q", "Q"):
            return False
        if key in ("?",):
            self.show_help()
        elif key in (curses.KEY_UP, "k", "K"):
            self.move(-1)
        elif key in (curses.KEY_DOWN, "j", "J"):
            self.move(1)
        elif key in (curses.KEY_LEFT, "h", "H"):
            self.focus = "sessions"
        elif key in (curses.KEY_RIGHT, "l", "L", "\t", curses.KEY_BTAB):
            self.focus = "panes" if self.focus == "sessions" else "sessions"
        elif key == curses.KEY_HOME:
            self.jump(False)
        elif key == curses.KEY_END:
            self.jump(True)
        elif key in ("\n", "\r", curses.KEY_ENTER, "o", "O"):
            self.attach()
        elif key in ("n", "N"):
            self.create_session()
        elif key in ("e", "E"):
            self.rename_session()
        elif key in ("d", "D", curses.KEY_DC):
            self.kill_session()
        elif key in ("s", "S"):
            self.send_line()
        elif key == "/":
            self.edit_filter()
        elif key in ("r", "R", "\x0c"):
            self.force_refresh = True
            self.set_message("Refreshing…")
        elif key in ("p", "P"):
            self.preview_enabled = not self.preview_enabled
            self._refresh_preview()
            self.set_message(
                f"Preview {'enabled' if self.preview_enabled else 'disabled'}"
            )
        elif key == "\x1b":
            if self.query:
                self.query = ""
                self.apply_filter()
                self._refresh_preview()
                self.set_message("Filter cleared")
            else:
                self.focus = "sessions"
        elif key == curses.KEY_MOUSE:
            self.handle_mouse()
        elif key == curses.KEY_RESIZE:
            self.screen.erase()
        return True

    def run(self) -> int:
        self.setup()
        while True:
            now = time.monotonic()
            if self.force_refresh or now - self.last_refresh >= self.refresh_interval:
                self.refresh()
            self.draw()
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            if not self.handle_key(key):
                return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tmux-tui",
        description="A friendly tmux manager powered by tmux-cli",
    )
    parser.add_argument(
        "--tb",
        metavar="PATH",
        help="tmux-cli tb.py or tb executable (default: bundled submodule)",
    )
    parser.add_argument(
        "--refresh",
        type=float,
        default=DEFAULT_REFRESH,
        metavar="SECONDS",
        help=f"refresh interval (default: {DEFAULT_REFRESH:g})",
    )
    parser.add_argument(
        "--preview-lines",
        type=int,
        default=DEFAULT_PREVIEW_LINES,
        metavar="N",
        help=f"scrollback lines fetched for preview (default: {DEFAULT_PREVIEW_LINES})",
    )
    parser.add_argument(
        "--no-preview",
        action="store_true",
        help="disable live pane capture",
    )
    parser.add_argument(
        "--session",
        help="select this session on startup",
    )
    parser.add_argument("--version", action="version", version=f"tmux-tui {VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.refresh <= 0:
        raise SystemExit("tmux-tui: --refresh must be positive")
    if args.preview_lines < 1:
        raise SystemExit("tmux-tui: --preview-lines must be positive")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("tmux-tui: an interactive terminal is required")
    try:
        backend = TmuxCLI.discover(args.tb)
    except BackendError as error:
        raise SystemExit(f"tmux-tui: {error.message}") from error

    def wrapped(stdscr: Any) -> int:
        return TmuxManager(
            stdscr,
            backend,
            refresh_interval=args.refresh,
            preview_lines=args.preview_lines,
            initial_session=args.session,
            preview_enabled=not args.no_preview,
        ).run()

    try:
        return curses.wrapper(wrapped)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
