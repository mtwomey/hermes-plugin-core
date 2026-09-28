"""Tests for venv resolution.

These assert the *contract* — "prefer a venv that can import hermes_cli,
prefer newest, treat the legacy fixed path as a last resort" — rather than
freezing today's directory names.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_plugin_core import venv as venv_mod


# ---------------------------------------------------------------------------
# Helpers — build fake venvs whose python3 either can or cannot import hermes_cli
# ---------------------------------------------------------------------------
def make_fake_python(path: Path, *, importable: bool) -> Path:
    """Create an executable stub that exits 0/1 for `-c 'import hermes_cli'`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    exit_code = 0 if importable else 1
    path.write_text(
        "#!/usr/bin/env bash\n"
        # Only the hermes_cli probe is interesting; anything else succeeds.
        'if [[ "$*" == *"import hermes_cli"* ]]; then\n'
        f"  exit {exit_code}\n"
        "fi\n"
        "exit 0\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def make_managed_venv(home: Path, env_hash: str, *, importable: bool = True) -> Path:
    py = home / "installs" / "abc123" / "environments" / env_hash / "venv" / "bin" / "python3"
    return make_fake_python(py, importable=importable)


def make_legacy_venv(home: Path, *, importable: bool = True) -> Path:
    return make_fake_python(home / "hermes-agent" / "venv" / "bin" / "python3", importable=importable)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated HERMES_HOME with no interpreter and no in-process hermes_cli."""
    h = tmp_path / "hermes_home"
    h.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(h))
    monkeypatch.delenv(venv_mod.ENV_OVERRIDE, raising=False)
    # hermes_venv_python() short-circuits to sys.executable when the plugin is
    # loaded in-process; suppress that so path resolution is what's tested.
    monkeypatch.delitem(sys.modules, "hermes_cli", raising=False)
    return h


# ---------------------------------------------------------------------------
# Resolution contract
# ---------------------------------------------------------------------------
def test_finds_managed_venv(home):
    expected = make_managed_venv(home, "envhash1")
    assert venv_mod.hermes_venv_python() == expected


def test_prefers_managed_over_legacy(home):
    """The legacy path survives an upgrade as a stale decoy — it must lose."""
    make_legacy_venv(home)
    managed = make_managed_venv(home, "envhash1")
    assert venv_mod.hermes_venv_python() == managed


def test_falls_back_to_legacy_when_no_managed_env(home):
    legacy = make_legacy_venv(home)
    assert venv_mod.hermes_venv_python() == legacy


def test_skips_venv_that_cannot_import_hermes_cli(home):
    """A venv directory is not proof; the probe decides."""
    make_managed_venv(home, "broken", importable=False)
    good = make_legacy_venv(home, importable=True)
    assert venv_mod.hermes_venv_python() == good


def test_prefers_newest_managed_env(home):
    """Two generations can coexist after an update; newest mtime wins."""
    old = make_managed_venv(home, "old")
    new = make_managed_venv(home, "new")
    old_dir, new_dir = old.parent.parent, new.parent.parent
    os.utime(old_dir, (1_000_000, 1_000_000))
    os.utime(new_dir, (2_000_000, 2_000_000))
    assert venv_mod.hermes_venv_python() == new


def test_raises_when_nothing_found(home):
    with pytest.raises(venv_mod.VenvNotFoundError) as exc:
        venv_mod.hermes_venv_python()
    # The error must name the override so the user has a way out.
    assert venv_mod.ENV_OVERRIDE in str(exc.value)


def test_env_override_wins(home, tmp_path, monkeypatch):
    make_managed_venv(home, "envhash1")
    override = make_fake_python(tmp_path / "custom" / "python3", importable=True)
    monkeypatch.setenv(venv_mod.ENV_OVERRIDE, str(override))
    assert venv_mod.hermes_venv_python() == override


def test_env_override_missing_file_raises(home, tmp_path, monkeypatch):
    monkeypatch.setenv(venv_mod.ENV_OVERRIDE, str(tmp_path / "nope" / "python3"))
    with pytest.raises(venv_mod.VenvNotFoundError):
        venv_mod.hermes_venv_python()


def test_in_process_uses_current_interpreter(home, monkeypatch):
    """Plugins load inside the venv already — trust sys.executable, skip probing."""
    make_managed_venv(home, "envhash1")
    monkeypatch.setitem(sys.modules, "hermes_cli", object())
    assert venv_mod.hermes_venv_python() == Path(sys.executable)


# ---------------------------------------------------------------------------
# Installer selection
# ---------------------------------------------------------------------------
def test_installer_prefers_uv_with_explicit_python(home, monkeypatch):
    """Managed venvs ship without pip, so uv must be targeted at --python."""
    uv = make_fake_python(home / "bin" / "uv", importable=True)
    py = make_managed_venv(home, "envhash1")

    cmd = venv_mod.installer_command(py)

    assert cmd[0] == str(uv)
    assert cmd[1:3] == ["pip", "install"]
    # Without --python, uv would install into its own environment.
    assert "--python" in cmd and str(py) in cmd


def test_installer_finds_versioned_uv_tool_dir(home, monkeypatch):
    uv = make_fake_python(home / "tools" / "uv-0.12.3-darwin-arm64" / "uv", importable=True)
    py = make_managed_venv(home, "envhash1")
    monkeypatch.setattr(venv_mod.shutil, "which", lambda _: None)

    assert venv_mod.installer_command(py)[0] == str(uv)


def test_installer_falls_back_to_pip(home, monkeypatch):
    """Legacy venvs have pip and no bundled uv."""
    py = make_legacy_venv(home)
    monkeypatch.setattr(venv_mod.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        venv_mod.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, b"", b""),
    )

    assert venv_mod.installer_command(py) == [str(py), "-m", "pip", "install"]


def test_installer_raises_without_uv_or_pip(home, monkeypatch):
    py = make_managed_venv(home, "envhash1")
    monkeypatch.setattr(venv_mod.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        venv_mod.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 1, b"", b""),
    )

    with pytest.raises(venv_mod.VenvNotFoundError):
        venv_mod.installer_command(py)


def test_install_packages_noop_on_empty_list(home):
    assert venv_mod.install_packages([], python=make_legacy_venv(home)).returncode == 0


def test_install_packages_editable_flags_each_package(home, monkeypatch):
    make_fake_python(home / "bin" / "uv", importable=True)
    py = make_managed_venv(home, "envhash1")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(venv_mod.subprocess, "run", fake_run)
    venv_mod.install_packages(["/repo/a", "/repo/b"], python=py, editable=True)

    assert captured["cmd"].count("-e") == 2
