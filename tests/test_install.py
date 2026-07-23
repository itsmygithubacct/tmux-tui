from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def make(prefix: Path, target: str, *, check: bool = True):
    return subprocess.run(
        ["make", target, f"PREFIX={prefix}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


class InstallTests(unittest.TestCase):
    def test_install_is_idempotent_and_uninstall_removes_only_managed_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"

            make(prefix, "install-tb")
            make(prefix, "install-tb")

            manager = prefix / "bin" / "tmux-tui"
            alias = prefix / "bin" / "tb"
            self.assertTrue(manager.is_symlink())
            self.assertTrue(alias.is_symlink())
            self.assertEqual(manager.resolve(), ROOT / "tmux_tui.py")
            self.assertEqual(alias.resolve(), ROOT / "tmux-cli" / "tb.py")

            make(prefix, "uninstall")
            self.assertFalse(manager.exists())
            self.assertFalse(alias.exists())

    def test_install_refuses_to_replace_a_regular_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            binary_dir = prefix / "bin"
            binary_dir.mkdir(parents=True)
            manager = binary_dir / "tmux-tui"
            manager.write_text("keep me\n")

            result = make(prefix, "install", check=False)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing to replace non-symlink", result.stderr)
            self.assertEqual(manager.read_text(), "keep me\n")

    def test_install_tb_preflights_alias_before_linking_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "prefix"
            binary_dir = prefix / "bin"
            binary_dir.mkdir(parents=True)
            alias = binary_dir / "tb"
            alias.write_text("keep me\n")

            result = make(prefix, "install-tb", check=False)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(alias.read_text(), "keep me\n")
            self.assertFalse((binary_dir / "tmux-tui").exists())

    def test_install_and_uninstall_leave_an_unmanaged_symlink_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            binary_dir = prefix / "bin"
            binary_dir.mkdir(parents=True)
            other = root / "other-manager"
            other.write_text("#!/bin/sh\n")
            manager = binary_dir / "tmux-tui"
            manager.symlink_to(other)

            result = make(prefix, "install", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing to replace unmanaged symlink", result.stderr)

            make(prefix, "uninstall")
            self.assertTrue(manager.is_symlink())
            self.assertEqual(manager.resolve(), other)

    def test_install_refuses_a_symlinked_bin_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prefix = root / "prefix"
            prefix.mkdir()
            elsewhere = root / "elsewhere"
            elsewhere.mkdir()
            (prefix / "bin").symlink_to(elsewhere, target_is_directory=True)

            result = make(prefix, "install", check=False)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing symlinked install directory", result.stderr)
            self.assertEqual(list(elsewhere.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
