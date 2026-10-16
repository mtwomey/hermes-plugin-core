"""
hermes_plugin_core.bundled_skills — extra skills shipped inside a plugin repo.

Every skill that documents a plugin (research notes, debugging playbooks, API
internals) should live in that plugin's repo so it is versioned with the code
it describes. Put each one at::

    <repo>/skills/<skill-name>/SKILL.md

``SetupCLI`` symlinks each to ``~/.hermes/skills/[<category>/]<skill-name>`` on
install, removes it on uninstall, and reports it in status; ``audit`` checks
it. This is separate from ``skill-stub/``, which is only a redirect to the
plugin's own ``<key>:<key>`` skill.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

BUNDLED_SKILLS_DIRNAME = "skills"


def hermes_skills_dir() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "skills"


def discover_bundled_skills(repo_dir: Path) -> list[str]:
    """Names of ``skills/<name>/`` dirs in the repo that contain a SKILL.md, sorted."""
    root = Path(repo_dir) / BUNDLED_SKILLS_DIRNAME
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith((".", "_")) and (p / "SKILL.md").is_file())


def resolve_bundled_skills(repo_dir: Path, configured: list[str] | None) -> list[str]:
    """``None`` → auto-discover; an explicit list (possibly empty) is used as-is."""
    return discover_bundled_skills(repo_dir) if configured is None else list(configured)


def bundled_skill_link(name: str, category: str = "") -> Path:
    base = hermes_skills_dir()
    return (base / category / name) if category else (base / name)


def bundled_skill_src(repo_dir: Path, name: str) -> Path:
    return Path(repo_dir) / BUNDLED_SKILLS_DIRNAME / name


def frontmatter_name(skill_md: Path) -> str | None:
    """Return the ``name:`` from a SKILL.md YAML frontmatter block, if any."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return None
    nm = re.search(r"^name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", m.group(1), re.M)
    return nm.group(1).strip() if nm else None
