"""Tests for bundled-skill support (<repo>/skills/<name>/SKILL.md → ~/.hermes/skills/<name>)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_plugin_core import audit, bundled_skills, setup_cli
from hermes_plugin_core.audit import AuditStatus


def _skill(repo: Path, name: str, fm_name: str | None = None) -> Path:
    d = repo / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {fm_name or name}\ndescription: x\n---\n# {name}\n")
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    (home / "skills").mkdir(parents=True)
    (home / "plugins").mkdir()
    (home / "config.yaml").write_text("plugins:\n  enabled: []\n")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(setup_cli, "HERMES_PLUGINS_DIR", home / "plugins")
    monkeypatch.setattr(setup_cli, "HERMES_SKILLS_DIR", home / "skills")
    repo = tmp_path / "hermes-plugin-demo"
    repo.mkdir()
    return SimpleNamespace(home=home, repo=repo)


def _cli(repo: Path, **kw) -> setup_cli.SetupCLI:
    cfg = setup_cli.PluginConfig(plugin_key="demo", service="hermes-demo", repo_dir=repo,
                                 keys=[], cred_prompts={}, **kw)
    return setup_cli.SetupCLI(cfg)


def test_discovery_ignores_dirs_without_skill_md(env):
    _skill(env.repo, "b-skill")
    _skill(env.repo, "a-skill")
    (env.repo / "skills" / "notes").mkdir()           # no SKILL.md
    (env.repo / "skills" / "_private").mkdir()
    (env.repo / "skills" / "_private" / "SKILL.md").write_text("x")
    assert bundled_skills.discover_bundled_skills(env.repo) == ["a-skill", "b-skill"]


def test_explicit_empty_list_disables_discovery(env):
    _skill(env.repo, "a-skill")
    assert bundled_skills.resolve_bundled_skills(env.repo, []) == []
    assert bundled_skills.resolve_bundled_skills(env.repo, None) == ["a-skill"]


def test_install_links_and_uninstall_removes(env):
    src = _skill(env.repo, "demo-research")
    cli = _cli(env.repo)
    cli.cmd_install(SimpleNamespace(yes=True))
    link = env.home / "skills" / "demo-research"
    assert link.is_symlink() and link.resolve() == src.resolve()

    cli.cmd_install(SimpleNamespace(yes=True))        # idempotent
    assert link.is_symlink()

    cli.cmd_uninstall(SimpleNamespace(yes=True))
    assert not link.exists() and not link.is_symlink()


def test_category_places_link_in_subdir(env):
    src = _skill(env.repo, "demo-research")
    _cli(env.repo, extra_skills_category="research").cmd_install(SimpleNamespace(yes=True))
    link = env.home / "skills" / "research" / "demo-research"
    assert link.is_symlink() and link.resolve() == src.resolve()


def test_install_refuses_to_clobber_real_dir(env):
    _skill(env.repo, "demo-research")
    loose = env.home / "skills" / "demo-research"
    loose.mkdir()
    (loose / "SKILL.md").write_text("user's unversioned copy")
    with pytest.raises(SystemExit):
        _cli(env.repo).cmd_install(SimpleNamespace(yes=True))
    assert (loose / "SKILL.md").read_text() == "user's unversioned copy"


def test_uninstall_leaves_foreign_symlink(env, tmp_path):
    _skill(env.repo, "demo-research")
    other = tmp_path / "elsewhere"
    other.mkdir()
    link = env.home / "skills" / "demo-research"
    link.symlink_to(other)
    _cli(env.repo).cmd_uninstall(SimpleNamespace(yes=True))
    assert link.is_symlink() and link.resolve() == other.resolve()


def test_audit_check_13(env):
    _skill(env.repo, "good")
    _skill(env.repo, "bad", fm_name="wrong-name")
    res = audit._check_bundled_skills(env.repo, None, "")
    by = {(r.label, r.status) for r in res}
    assert ("bundled skill 'good' (repo)", AuditStatus.PASS) in by
    assert ("bundled skill 'bad'", AuditStatus.FAIL) in by
    assert ("bundled skill 'good' (install)", AuditStatus.WARN) in by   # not linked yet
    (env.home / "skills" / "good").symlink_to(env.repo / "skills" / "good")
    res = audit._check_bundled_skills(env.repo, None, "")
    assert any(r.label == "bundled skill 'good' (install)" and r.status == AuditStatus.PASS for r in res)
