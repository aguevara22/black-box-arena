"""ensure_toolchain: the explicit one-time fetch that replaces elan's silent
first-use download (which used to blow the smoke's 120 s health deadline)."""
import stat
from pathlib import Path

import pytest

from arena import lean_workspace

PIN = "leanprover/lean4:v4.33.0"


def _challenge(tmp_path: Path) -> Path:
    src = tmp_path / "challenge"
    src.mkdir()
    (src / "lean-toolchain").write_text(PIN + "\n", encoding="utf-8")
    return src


def _fake_elan(tmp_path: Path, installed: list[str]) -> tuple[Path, Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    listing = tmp_path / "installed.txt"
    listing.write_text("".join(line + "\n" for line in installed), encoding="utf-8")
    calls = tmp_path / "calls.log"
    script = bindir / "elan"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        f'if [ "$1" = toolchain ] && [ "$2" = list ]; then cat "{listing}"; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return bindir, calls


def test_installed_toolchain_is_left_alone(tmp_path, monkeypatch):
    bindir, calls = _fake_elan(tmp_path, [PIN + " (default)", "leanprover/lean4:v4.34.0-rc2"])
    monkeypatch.setattr(lean_workspace, "_elan_env", lambda: {"PATH": f"{bindir}:/usr/bin:/bin"})
    said: list[str] = []
    assert lean_workspace.ensure_toolchain(_challenge(tmp_path), said.append) == PIN
    assert calls.read_text().splitlines() == ["toolchain list"]
    assert said == []


def test_missing_toolchain_is_fetched_explicitly(tmp_path, monkeypatch):
    bindir, calls = _fake_elan(tmp_path, ["leanprover/lean4:v4.34.0-rc2"])
    monkeypatch.setattr(lean_workspace, "_elan_env", lambda: {"PATH": f"{bindir}:/usr/bin:/bin"})
    said: list[str] = []
    assert lean_workspace.ensure_toolchain(_challenge(tmp_path), said.append) == PIN
    assert calls.read_text().splitlines() == ["toolchain list", f"toolchain install {PIN}"]
    assert len(said) == 2 and "not installed" in said[0] and "installed" in said[1]


def test_missing_elan_names_the_install_command(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(lean_workspace, "_elan_env", lambda: {"PATH": str(empty)})
    with pytest.raises(lean_workspace.WorkspaceError, match="elan-init.sh"):
        lean_workspace.ensure_toolchain(_challenge(tmp_path))
