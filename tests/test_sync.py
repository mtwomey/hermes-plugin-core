from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_plugin_core import sync as sync_mod


def git(cwd: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=check,
    )


def configure_git_identity(repo: Path) -> None:
    git(repo, "config", "user.name", "Hermes Test", check=True)
    git(repo, "config", "user.email", "hermes-test@example.com", check=True)


def write_setup_sh(repo: Path) -> None:
    script = """#!/usr/bin/env bash
set -euo pipefail
ACTION="${2:-}"
echo "$ACTION" >> .setup-log
case "$ACTION" in
  install)
    [[ "${FAIL_INSTALL:-0}" == "1" ]] && exit 1
    ;;
  test)
    [[ "${FAIL_TEST:-0}" == "1" ]] && exit 1
    ;;
  audit)
    [[ "${FAIL_AUDIT:-0}" == "1" ]] && exit 1
    ;;
esac
exit 0
"""
    path = repo / "setup.sh"
    path.write_text(script)
    path.chmod(0o755)


def init_bare_remote(tmp_path: Path, name: str) -> Path:
    remote = tmp_path / f"{name}.git"
    git(remote.parent, "init", "--bare", "--initial-branch=main", str(remote))
    return remote


def create_plugin_remote(tmp_path: Path, name: str = "plugin") -> tuple[Path, Path, str]:
    remote = init_bare_remote(tmp_path, name)
    src = tmp_path / f"{name}-src"
    git(tmp_path, "clone", str(remote), str(src))
    configure_git_identity(src)
    (src / "repo.txt").write_text("v1\n")
    write_setup_sh(src)
    git(src, "add", ".")
    git(src, "commit", "-m", "chore: initial plugin")
    git(src, "push", "origin", "main")
    commit = git(src, "rev-parse", "HEAD").stdout.strip()
    return remote, src, commit


def create_local_plugin_clone(tmp_path: Path, remote: Path, name: str = "plugin") -> Path:
    local = tmp_path / f"{name}-local"
    git(tmp_path, "clone", str(remote), str(local))
    configure_git_identity(local)
    return local


def create_homes_and_link(tmp_path: Path, plugin_name: str, plugin_repo: Path) -> Path:
    home = tmp_path / "home"
    plugins = home / "plugins"
    plugins.mkdir(parents=True)
    (plugins / plugin_name).symlink_to(plugin_repo)
    return home


def advance_remote_commit(remote_src: Path, filename: str = "repo.txt", content: str = "v2\n") -> str:
    (remote_src / filename).write_text(content)
    git(remote_src, "add", filename)
    git(remote_src, "commit", "-m", "feat: remote update")
    git(remote_src, "push", "origin", "main")
    return git(remote_src, "rev-parse", "HEAD").stdout.strip()


def test_update_core_builds_expected_pip_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    venv_python = home / "hermes-agent" / "venv" / "bin" / "python3"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/usr/bin/env python3\n")
    venv_python.chmod(0o755)

    captured: dict[str, object] = {}

    def fake_run_cmd(args, cwd=None, timeout=300, dry_run=False):
        captured["args"] = list(args)
        captured["cwd"] = cwd
        return sync_mod.CommandResult(command=list(args), cwd=str(cwd), exit_code=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(sync_mod, "run_cmd", fake_run_cmd)
    monkeypatch.setattr(sync_mod, "_resolve_venv_python", lambda h: venv_python)

    result = sync_mod.update_core(home, skip_core=False, dry_run=False)
    assert result.status == "updated"
    assert captured["args"][0] == str(venv_python)
    assert captured["args"][1:5] == ["-m", "pip", "install", "--upgrade"]
    assert captured["args"][-1] == sync_mod.CORE_GITHUB_URL


def test_discover_plugins_skips_filter_and_keeps_symlink_target(tmp_path: Path) -> None:
    remote, src, _ = create_plugin_remote(tmp_path, "one")
    local = create_local_plugin_clone(tmp_path, remote, "one")
    home = create_homes_and_link(tmp_path, "one", local)
    extra = home / "plugins" / "two"
    extra.mkdir(parents=True)

    discovered = sync_mod.discover_plugins(home, selected={"one"})
    assert discovered == [("one", local)]


def test_sync_plugin_skips_dirty_repo(tmp_path: Path) -> None:
    remote, src, _ = create_plugin_remote(tmp_path, "dirty")
    local = create_local_plugin_clone(tmp_path, remote, "dirty")
    home = create_homes_and_link(tmp_path, "dirty", local)
    (local / "repo.txt").write_text("dirty change\n")

    result = sync_mod.sync_plugin("dirty", local)
    assert result.status == "skipped_dirty"
    assert "uncommitted" in (result.reason or "")


def test_sync_plugin_skips_non_main_branch(tmp_path: Path) -> None:
    remote, src, _ = create_plugin_remote(tmp_path, "branchy")
    local = create_local_plugin_clone(tmp_path, remote, "branchy")
    git(local, "checkout", "-b", "feature/demo")

    result = sync_mod.sync_plugin("branchy", local)
    assert result.status == "skipped_non_main_branch"
    assert result.branch == "feature/demo"


def test_sync_plugin_updates_and_rollback_on_test_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote, src, old_commit = create_plugin_remote(tmp_path, "update")
    local = create_local_plugin_clone(tmp_path, remote, "update")
    home = create_homes_and_link(tmp_path, "update", local)
    new_commit = advance_remote_commit(src)
    monkeypatch.setenv("FAIL_TEST", "1")

    result = sync_mod.sync_plugin("update", local)
    assert result.status == "failed_test_rolled_back"
    assert result.failed_step == "test"
    assert result.rollback_status == "succeeded"
    assert result.old_commit == old_commit
    assert result.attempted_commit == new_commit
    assert git(local, "rev-parse", "HEAD").stdout.strip() == old_commit
    assert result.checks["install"].status == "passed"
    assert result.checks["test"].status == "failed"


def test_run_sync_updates_one_plugin_and_skips_another(tmp_path: Path) -> None:
    remote_a, src_a, _ = create_plugin_remote(tmp_path, "alpha")
    local_a = create_local_plugin_clone(tmp_path, remote_a, "alpha")
    remote_b, src_b, _ = create_plugin_remote(tmp_path, "beta")
    local_b = create_local_plugin_clone(tmp_path, remote_b, "beta")
    home = tmp_path / "home"
    plugins = home / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "alpha").symlink_to(local_a)
    (plugins / "beta").symlink_to(local_b)
    advance_remote_commit(src_a, content="alpha v2\n")

    result, exit_code = sync_mod.run_sync(
        sync_mod.SyncOptions(hermes_home=home, plugins=["alpha"], skip_core=True)
    )

    assert exit_code == 0
    assert result.summary.total == 2
    assert result.summary.updated == 1
    assert result.summary.skipped == 1
    assert any(p.name == "alpha" and p.status == "updated" for p in result.plugins)
    assert any(p.name == "beta" and p.status == "skipped_not_selected" for p in result.plugins)
    assert result.restart_required is True


def test_run_sync_continues_after_one_plugin_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote_a, src_a, _ = create_plugin_remote(tmp_path, "good")
    local_a = create_local_plugin_clone(tmp_path, remote_a, "good")
    remote_b, src_b, _ = create_plugin_remote(tmp_path, "bad")
    local_b = create_local_plugin_clone(tmp_path, remote_b, "bad")
    home = tmp_path / "home"
    plugins = home / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "good").symlink_to(local_a)
    (plugins / "bad").symlink_to(local_b)
    advance_remote_commit(src_a, content="good v2\n")
    advance_remote_commit(src_b, content="bad v2\n")
    monkeypatch.setenv("FAIL_TEST", "1")

    result, exit_code = sync_mod.run_sync(sync_mod.SyncOptions(hermes_home=home, skip_core=True))

    assert exit_code == 1
    assert any(p.name == "good" and p.status == "failed_test_rolled_back" for p in result.plugins)
    assert any(p.name == "bad" and p.status == "failed_test_rolled_back" for p in result.plugins)
    assert result.summary.failed == 2
    assert result.summary.rolled_back == 2
    assert result.restart_required is False


def test_renderers_produce_valid_human_and_json_output(tmp_path: Path) -> None:
    remote, src, _ = create_plugin_remote(tmp_path, "render")
    local = create_local_plugin_clone(tmp_path, remote, "render")
    home = create_homes_and_link(tmp_path, "render", local)
    advance_remote_commit(src, content="render v2\n")
    result, _ = sync_mod.run_sync(sync_mod.SyncOptions(hermes_home=home, skip_core=True))

    human = sync_mod.render_human(result)
    assert "hermes-plugin-sync" in human
    assert "render" in human
    assert "Restart required" in human

    data = json.loads(sync_mod.render_json(result))
    assert data["hermes_home"] == str(home)
    assert data["summary"]["updated"] == 1
    assert data["plugins"][0]["name"] == "render"


def test_run_sync_strict_counts_skips_as_failure(tmp_path: Path) -> None:
    remote, src, _ = create_plugin_remote(tmp_path, "strict")
    local = create_local_plugin_clone(tmp_path, remote, "strict")
    git(local, "checkout", "-b", "feature/demo")
    home = create_homes_and_link(tmp_path, "strict", local)

    result, exit_code = sync_mod.run_sync(sync_mod.SyncOptions(hermes_home=home, skip_core=True, strict=True))

    assert exit_code == 1
    assert result.plugins[0].status == "skipped_non_main_branch"


def test_run_sync_core_failure_returns_global_exit_code_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    monkeypatch.setattr(sync_mod, "update_core", lambda home, skip_core, dry_run=False: sync_mod.CoreSyncResult(status="failed", exit_code=1, details="boom"))

    result, exit_code = sync_mod.run_sync(sync_mod.SyncOptions(hermes_home=home))
    assert exit_code == 2
    assert result.status == "failed"
    assert result.core.status == "failed"
