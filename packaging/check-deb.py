#!/usr/bin/env python3
"""check-deb.py DEB : the tmux-tui archive package uses packaged tb."""

import subprocess
import sys
import tempfile
from pathlib import Path


def fail(message: str) -> None:
    print(f"check-deb: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    if len(sys.argv) != 2:
        fail("usage: check-deb.py DEB")
    deb = Path(sys.argv[1])
    fields = subprocess.run(
        ["dpkg-deb", "-f", str(deb), "Package", "Version", "Architecture", "Depends"],
        check=True, capture_output=True, text=True).stdout
    if "Package: tmux-tui\n" not in fields:
        fail(f"unexpected package fields:\n{fields}")
    if "Version: 0.2.0+packaging1\n" not in fields:
        fail(f"unexpected version:\n{fields}")
    if "Architecture: all\n" not in fields:
        fail(f"unexpected architecture:\n{fields}")
    if "tmux-cli" not in fields:
        fail(f"missing tmux-cli dependency:\n{fields}")
    with tempfile.TemporaryDirectory(prefix="tmux-tui-deb.") as raw:
        root = Path(raw)
        subprocess.run(["dpkg-deb", "-x", str(deb), str(root)], check=True)
        link = root / "usr/bin/tmux-tui"
        if not link.is_symlink() or link.readlink() != Path("../share/tmux-tui/tmux_tui.py"):
            fail(f"command link is {link.readlink() if link.is_symlink() else 'missing'}")
        program = root / "usr/share/tmux-tui/tmux_tui.py"
        if not program.is_file():
            fail("program missing")
        if (root / "usr/share/tmux-tui/tmux-cli").exists():
            fail("tmux-cli submodule was packaged")
        text = program.read_text()
        if "shutil.which(\"tb\")" not in text:
            fail("packaged program no longer discovers tb on PATH")
    print("check-deb: passed")


if __name__ == "__main__":
    main()
