"""Locate the live Hermes venv and its package installer.

Hermes moved to a package-managed layout where the runtime venv lives at
``$HERMES_HOME/installs/<install-id>/environments/<env-hash>/venv`` and is
**rebuilt from scratch** by ``hermes update``. The old fixed location
(``~/.hermes/hermes-agent/venv``) still exists on upgraded machines but is
stale, so a hardcoded path silently installs into a venv nothing runs from.

Resolution is therefore dynamic, and "is this really Hermes' venv?" is
answered by probing for the ``hermes_cli`` package rather than by trusting a
path shape.

The rebuilt venv also ships **without pip**, so use :func:`installer_command`
rather than assuming ``python -m pip`` works.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

__all__ = [
    "hermes_home",
    "hermes_venv_python",
    "installer_command",
    "install_packages",
    "VenvNotFoundError",
]

#: Escape hatch for unusual layouts and for tests.
ENV_OVERRIDE = "HERMES_PLUGIN_VENV_PYTHON"

#: Pre-package-manager location. Kept last in the search order: on an upgraded
#: machine it exists but is not what the running agent imports from.
LEGACY_VENV_PYTHON = Path("hermes-agent") / "venv" / "bin" / "python3"

_PROBE = "import hermes_cli"


class VenvNotFoundError(RuntimeError):
    """No interpreter that can import ``hermes_cli`` could be located."""


def hermes_home() -> Path:
    """Return ``$HERMES_HOME``, falling back to ``~/.hermes``."""
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _bin_dir(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def _python_in(venv: Path) -> Path:
    exe = "python.exe" if os.name == "nt" else "python3"
    return _bin_dir(venv) / exe


def _is_hermes_python(python: Path) -> bool:
    """True if ``python`` exists and can import ``hermes_cli``."""
    if not python.is_file():
        return False
    try:
        return subprocess.run(
            [str(python), "-c", _PROBE],
            capture_output=True,
            timeout=30,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _candidates() -> list[Path]:
    """Managed-runtime interpreters, newest environment first."""
    roots = sorted(
        hermes_home().glob("installs/*/environments/*/venv"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    return [_python_in(root) for root in roots]


def hermes_venv_python() -> Path:
    """Return the interpreter of the venv the running Hermes imports from.

    Order: explicit override, the current interpreter (plugins load in-process,
    so this is the common and cheapest hit), then managed runtimes newest
    first, then the legacy fixed path.

    Raises:
        VenvNotFoundError: if nothing can import ``hermes_cli``.
    """
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        python = Path(override).expanduser()
        if not python.is_file():
            raise VenvNotFoundError(f"{ENV_OVERRIDE} points at a missing file: {python}")
        return python

    # In-process: we are already running under the venv we want.
    if "hermes_cli" in sys.modules:
        return Path(sys.executable)

    for python in [*_candidates(), hermes_home() / LEGACY_VENV_PYTHON]:
        if _is_hermes_python(python):
            return python

    raise VenvNotFoundError(
        "Could not find the Hermes venv. Looked under "
        f"{hermes_home()}/installs/*/environments/*/venv and "
        f"{hermes_home() / LEGACY_VENV_PYTHON}. "
        f"Set {ENV_OVERRIDE} to override."
    )


def installer_command(python: Path | None = None) -> list[str]:
    """Return an argv prefix that installs packages into ``python``'s env.

    Managed venvs are built by uv and contain no pip, so uv is preferred and
    pointed at the target interpreter explicitly. Falls back to ``python -m
    pip`` for legacy venvs.

    Raises:
        VenvNotFoundError: if neither uv nor pip is usable.
    """
    python = python or hermes_venv_python()

    uv = _find_uv()
    if uv:
        return [str(uv), "pip", "install", "--python", str(python)]

    has_pip = subprocess.run(
        [str(python), "-c", "import pip"], capture_output=True
    ).returncode == 0
    if has_pip:
        return [str(python), "-m", "pip", "install"]

    raise VenvNotFoundError(
        f"Neither uv nor pip is available to install into {python}. "
        "Expected uv at $HERMES_HOME/bin/uv or on PATH."
    )


def _find_uv() -> Path | None:
    home = hermes_home()
    bundled = home / "bin" / "uv"
    if bundled.is_file():
        return bundled

    # Versioned tool dir, e.g. tools/uv-0.12.3-darwin-arm64/uv
    tools = sorted(home.glob("tools/uv-*/uv"), reverse=True)
    if tools:
        return tools[0]

    found = shutil.which("uv")
    return Path(found) if found else None


def install_packages(
    packages: list[str],
    python: Path | None = None,
    editable: bool = False,
) -> subprocess.CompletedProcess:
    """Install ``packages`` into the Hermes venv. Returns the finished process.

    Callers inspect ``returncode``/``stderr``; nothing is raised on a failed
    install so the caller can format its own error.
    """
    if not packages:
        return subprocess.CompletedProcess([], 0, "", "")

    python = python or hermes_venv_python()
    cmd = installer_command(python)
    if editable:
        for pkg in packages:
            cmd += ["-e", pkg]
    else:
        cmd += packages

    return subprocess.run(cmd, capture_output=True, text=True)
