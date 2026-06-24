"""hermes_plugin_core.sync — update installed Hermes plugins in place.

This module provides the ``hermes-plugin-sync`` console command.  It updates
``hermes-plugin-core`` first, then discovers locally installed plugins under
``$HERMES_HOME/plugins`` and synchronizes each Git-backed plugin repo from
``origin/main`` using fast-forward-only semantics.

Safety policy:
- skip dirty repos
- skip non-main branches
- continue after failures
- roll back post-pull validation failures to the previous commit
- report that Hermes restart is required after successful code changes
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from hermes_plugin_core.config import hermes_home

CORE_GITHUB_URL = "git+https://github.com/mtwomey/hermes-plugin-core"
DEFAULT_VENV_PYTHON_RELATIVE = Path("hermes-agent") / "venv"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    command: list[str]
    cwd: str | None
    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class CheckResult:
    status: str
    exit_code: int | None = None
    command: list[str] | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""


@dataclass
class CoreSyncResult:
    status: str
    command: list[str] | None = None
    exit_code: int | None = None
    details: str | None = None


@dataclass
class PluginSyncResult:
    name: str
    path: str
    status: str
    branch: str | None = None
    old_commit: str | None = None
    new_commit: str | None = None
    attempted_commit: str | None = None
    reason: str | None = None
    failed_step: str | None = None
    rollback_status: str | None = None
    checks: dict[str, CheckResult] = field(default_factory=dict)
    restart_required: bool = False


@dataclass
class SyncSummary:
    total: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    rolled_back: int = 0


@dataclass
class SyncRunResult:
    status: str
    hermes_home: str
    restart_required: bool
    core: CoreSyncResult
    plugins: list[PluginSyncResult]
    summary: SyncSummary


@dataclass
class SyncOptions:
    hermes_home: Path | None = None
    plugins: list[str] | None = None
    json_output: bool = False
    dry_run: bool = False
    verbose: bool = False
    skip_core: bool = False
    strict: bool = False
    run_install: bool = True
    run_test: bool = True
    run_audit: bool = True


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _tail_text(text: str, limit: int = 2500) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _jsonable(value):
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if hasattr(value, "__dataclass_fields__"):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    return value


def _resolve_home(home: Path | None = None) -> Path:
    return home or hermes_home()


def _resolve_venv_python(home: Path) -> Path:
    if sys.platform == "win32":
        return home / DEFAULT_VENV_PYTHON_RELATIVE / "Scripts" / "python.exe"
    return home / DEFAULT_VENV_PYTHON_RELATIVE / "bin" / "python3"


def _plugin_root_dir(home: Path) -> Path:
    return home / "plugins"


def _setup_script_command(setup_sh: Path) -> list[str]:
    if os.access(setup_sh, os.X_OK):
        return [str(setup_sh)]
    return ["bash", str(setup_sh)]


def run_cmd(
    args: Sequence[str],
    cwd: Path | None = None,
    timeout: int = 300,
    dry_run: bool = False,
) -> CommandResult:
    cmd = list(args)
    if dry_run:
        return CommandResult(
            command=cmd,
            cwd=str(cwd) if cwd else None,
            exit_code=0,
            stdout=f"DRY-RUN: {' '.join(cmd)}",
            stderr="",
        )
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(
        command=cmd,
        cwd=str(cwd) if cwd else None,
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _git(path: Path, *args: str, dry_run: bool = False, timeout: int = 300) -> CommandResult:
    return run_cmd(["git", "-C", str(path), *args], cwd=path, timeout=timeout, dry_run=dry_run)


def _run_setup(path: Path, action: str, dry_run: bool = False) -> CommandResult:
    setup_sh = path / "setup.sh"
    if not setup_sh.exists():
        return CommandResult(
            command=["<missing>", action],
            cwd=str(path),
            exit_code=1,
            stdout="",
            stderr=f"missing setup.sh at {setup_sh}",
        )
    cmd = _setup_script_command(setup_sh) + ["--yes", action]
    return run_cmd(cmd, cwd=path, dry_run=dry_run)


def _command_check(result: CommandResult, status: str) -> CheckResult:
    return CheckResult(
        status=status,
        exit_code=result.exit_code,
        command=result.command,
        stdout_tail=_tail_text(result.stdout),
        stderr_tail=_tail_text(result.stderr),
    )


def _is_git_repo(path: Path) -> bool:
    result = _git(path, "rev-parse", "--is-inside-work-tree")
    return result.exit_code == 0 and result.stdout.strip().lower() == "true"


def _current_branch(path: Path) -> str | None:
    result = _git(path, "branch", "--show-current")
    if result.exit_code != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def _current_commit(path: Path) -> str | None:
    result = _git(path, "rev-parse", "HEAD")
    if result.exit_code != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def _is_dirty(path: Path) -> bool:
    result = _git(path, "status", "--porcelain")
    if result.exit_code != 0:
        return True
    return bool(result.stdout.strip())


def _fetch_origin_main(path: Path, dry_run: bool = False) -> CommandResult:
    return _git(path, "fetch", "origin", "main", dry_run=dry_run)


def _remote_main_commit(path: Path, dry_run: bool = False) -> str | None:
    if dry_run:
        result = run_cmd(["git", "-C", str(path), "ls-remote", "--heads", "origin", "main"], cwd=path, dry_run=False)
        if result.exit_code != 0:
            return None
        first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        commit = first_line.split()[0] if first_line else ""
        return commit or None

    result = _git(path, "rev-parse", "origin/main")
    if result.exit_code != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def _is_ancestor(path: Path, ancestor: str, descendant: str) -> bool:
    result = _git(path, "merge-base", "--is-ancestor", ancestor, descendant)
    return result.exit_code == 0


def _is_selected(name: str, selected: set[str] | None) -> bool:
    return selected is None or name in selected


# ---------------------------------------------------------------------------
# Discovery and update steps
# ---------------------------------------------------------------------------

def discover_plugins(home: Path, selected: set[str] | None = None) -> list[tuple[str, Path]]:
    plugins_dir = _plugin_root_dir(home)
    if not plugins_dir.exists():
        return []

    entries: list[tuple[str, Path]] = []
    for child in sorted(plugins_dir.iterdir(), key=lambda p: p.name):
        if not _is_selected(child.name, selected):
            continue
        if child.is_symlink():
            try:
                resolved = child.resolve(strict=True)
            except FileNotFoundError:
                resolved = child
        else:
            resolved = child
        entries.append((child.name, resolved))
    return entries


def update_core(home: Path, skip_core: bool, dry_run: bool = False) -> CoreSyncResult:
    if skip_core:
        return CoreSyncResult(status="skipped", details="--skip-core requested")

    venv_python = _resolve_venv_python(home)
    cmd = [str(venv_python), "-m", "pip", "install", "--upgrade", CORE_GITHUB_URL]
    if not venv_python.exists():
        return CoreSyncResult(status="failed", command=cmd, exit_code=1, details=f"missing Hermes venv Python at {venv_python}")

    result = run_cmd(cmd, cwd=home, dry_run=dry_run)
    if result.exit_code != 0:
        return CoreSyncResult(
            status="failed",
            command=result.command,
            exit_code=result.exit_code,
            details=_tail_text(result.stderr or result.stdout),
        )
    status = "updated" if not dry_run else "dry_run"
    return CoreSyncResult(
        status=status,
        command=result.command,
        exit_code=result.exit_code,
        details=_tail_text(result.stdout) or ("dry-run" if dry_run else "pip upgrade completed"),
    )


def _sync_plugin_checks(path: Path, dry_run: bool) -> dict[str, CheckResult]:
    checks: dict[str, CheckResult] = {}
    for action, label in (("install", "install"), ("test", "test"), ("audit", "audit")):
        result = _run_setup(path, action, dry_run=dry_run)
        checks[label] = _command_check(result, "passed" if result.exit_code == 0 else "failed")
        if result.exit_code != 0:
            break
    return checks


def _rollback_plugin(path: Path, old_commit: str, dry_run: bool = False) -> tuple[str, CommandResult | None, CommandResult | None]:
    reset_result = _git(path, "reset", "--hard", old_commit, dry_run=dry_run)
    if reset_result.exit_code != 0:
        return "reset_failed", reset_result, None
    reinstall_result = _run_setup(path, "install", dry_run=dry_run)
    if reinstall_result.exit_code != 0:
        return "reinstall_failed", reset_result, reinstall_result
    return "succeeded", reset_result, reinstall_result


def sync_plugin(
    name: str,
    path: Path,
    *,
    dry_run: bool = False,
    run_install: bool = True,
    run_test: bool = True,
    run_audit: bool = True,
) -> PluginSyncResult:
    if not path.exists():
        return PluginSyncResult(name=name, path=str(path), status="skipped_non_git", reason="path does not exist")

    if not _is_git_repo(path):
        return PluginSyncResult(name=name, path=str(path), status="skipped_non_git", reason="not a git repo")

    branch = _current_branch(path)
    if branch != "main":
        why = "detached HEAD" if branch is None else f"current branch is {branch!r}"
        return PluginSyncResult(name=name, path=str(path), status="skipped_non_main_branch", branch=branch, reason=why)

    if _is_dirty(path):
        return PluginSyncResult(name=name, path=str(path), status="skipped_dirty", branch=branch, reason="working tree has uncommitted changes")

    old_commit = _current_commit(path)
    if not old_commit:
        return PluginSyncResult(name=name, path=str(path), status="failed_fetch", branch=branch, reason="unable to read current commit")

    fetch_result = _fetch_origin_main(path, dry_run=dry_run)
    if fetch_result.exit_code != 0:
        return PluginSyncResult(
            name=name,
            path=str(path),
            status="failed_fetch",
            branch=branch,
            old_commit=old_commit,
            reason=_tail_text(fetch_result.stderr or fetch_result.stdout) or "git fetch origin main failed",
        )

    remote_commit = _remote_main_commit(path, dry_run=dry_run)
    if not remote_commit:
        return PluginSyncResult(
            name=name,
            path=str(path),
            status="failed_fetch",
            branch=branch,
            old_commit=old_commit,
            reason="unable to resolve origin/main",
        )

    if old_commit == remote_commit:
        return PluginSyncResult(
            name=name,
            path=str(path),
            status="unchanged",
            branch=branch,
            old_commit=old_commit,
            new_commit=remote_commit,
        )

    if dry_run:
        return PluginSyncResult(
            name=name,
            path=str(path),
            status="would_update",
            branch=branch,
            old_commit=old_commit,
            new_commit=remote_commit,
            attempted_commit=remote_commit,
            reason="dry-run; no changes applied",
        )

    if not _is_ancestor(path, old_commit, remote_commit):
        return PluginSyncResult(
            name=name,
            path=str(path),
            status="failed_fast_forward",
            branch=branch,
            old_commit=old_commit,
            attempted_commit=remote_commit,
            reason="local HEAD is not an ancestor of origin/main",
        )

    merge_result = _git(path, "merge", "--ff-only", "origin/main", dry_run=dry_run)
    if merge_result.exit_code != 0:
        return PluginSyncResult(
            name=name,
            path=str(path),
            status="failed_fast_forward",
            branch=branch,
            old_commit=old_commit,
            attempted_commit=remote_commit,
            reason=_tail_text(merge_result.stderr or merge_result.stdout) or "git merge --ff-only origin/main failed",
        )

    new_commit = remote_commit if dry_run else _current_commit(path) or remote_commit
    result = PluginSyncResult(
        name=name,
        path=str(path),
        status="updated",
        branch=branch,
        old_commit=old_commit,
        new_commit=new_commit,
        attempted_commit=remote_commit,
        restart_required=True,
    )

    if dry_run:
        result.status = "would_update"
        result.reason = "dry-run; no changes applied"
        result.restart_required = False
        return result

    if not (run_install or run_test or run_audit):
        return result

    checks: dict[str, CheckResult] = {}
    if run_install:
        install_result = _run_setup(path, "install", dry_run=dry_run)
        checks["install"] = _command_check(install_result, "passed" if install_result.exit_code == 0 else "failed")
        if install_result.exit_code != 0:
            rollback_status, reset_result, reinstall_result = _rollback_plugin(path, old_commit, dry_run=dry_run)
            result.status = "failed_install_rolled_back"
            result.failed_step = "install"
            result.rollback_status = rollback_status
            result.checks = checks
            if reset_result is not None:
                checks["rollback_reset"] = _command_check(reset_result, "passed" if reset_result.exit_code == 0 else "failed")
            if reinstall_result is not None:
                checks["rollback_install"] = _command_check(reinstall_result, "passed" if reinstall_result.exit_code == 0 else "failed")
            result.restart_required = False
            return result

    if run_test:
        test_result = _run_setup(path, "test", dry_run=dry_run)
        checks["test"] = _command_check(test_result, "passed" if test_result.exit_code == 0 else "failed")
        if test_result.exit_code != 0:
            rollback_status, reset_result, reinstall_result = _rollback_plugin(path, old_commit, dry_run=dry_run)
            result.status = "failed_test_rolled_back"
            result.failed_step = "test"
            result.rollback_status = rollback_status
            result.checks = checks
            if reset_result is not None:
                checks["rollback_reset"] = _command_check(reset_result, "passed" if reset_result.exit_code == 0 else "failed")
            if reinstall_result is not None:
                checks["rollback_install"] = _command_check(reinstall_result, "passed" if reinstall_result.exit_code == 0 else "failed")
            result.restart_required = False
            return result

    if run_audit:
        audit_result = _run_setup(path, "audit", dry_run=dry_run)
        checks["audit"] = _command_check(audit_result, "passed" if audit_result.exit_code == 0 else "failed")
        if audit_result.exit_code != 0:
            rollback_status, reset_result, reinstall_result = _rollback_plugin(path, old_commit, dry_run=dry_run)
            result.status = "failed_audit_rolled_back"
            result.failed_step = "audit"
            result.rollback_status = rollback_status
            result.checks = checks
            if reset_result is not None:
                checks["rollback_reset"] = _command_check(reset_result, "passed" if reset_result.exit_code == 0 else "failed")
            if reinstall_result is not None:
                checks["rollback_install"] = _command_check(reinstall_result, "passed" if reinstall_result.exit_code == 0 else "failed")
            result.restart_required = False
            return result

    result.checks = checks
    return result


# ---------------------------------------------------------------------------
# Run orchestration and rendering
# ---------------------------------------------------------------------------

def _summary_for_plugins(plugins: list[PluginSyncResult]) -> SyncSummary:
    summary = SyncSummary(total=len(plugins))
    for plugin in plugins:
        if plugin.status == "updated":
            summary.updated += 1
        elif plugin.status == "unchanged":
            summary.unchanged += 1
        elif plugin.status.startswith("skipped") or plugin.status.startswith("would_"):
            summary.skipped += 1
        elif plugin.status.startswith("failed"):
            summary.failed += 1
            if plugin.rollback_status == "succeeded":
                summary.rolled_back += 1
    return summary


def run_sync(options: SyncOptions) -> tuple[SyncRunResult, int]:
    home = _resolve_home(options.hermes_home)
    selected = set(options.plugins) if options.plugins else None

    core = update_core(home, skip_core=options.skip_core, dry_run=options.dry_run)
    if core.status == "failed":
        result = SyncRunResult(
            status="failed",
            hermes_home=str(home),
            restart_required=False,
            core=core,
            plugins=[],
            summary=SyncSummary(),
        )
        return result, 2

    discovered = discover_plugins(home)
    plugins: list[PluginSyncResult] = []

    for name, path in discovered:
        if selected is not None and name not in selected:
            plugins.append(
                PluginSyncResult(
                    name=name,
                    path=str(path),
                    status="skipped_not_selected",
                    reason="not selected by --plugins",
                )
            )
            continue
        plugins.append(
            sync_plugin(
                name,
                path,
                dry_run=options.dry_run,
                run_install=options.run_install,
                run_test=options.run_test,
                run_audit=options.run_audit,
            )
        )

    summary = _summary_for_plugins(plugins)
    restart_required = core.status == "updated" or any(p.restart_required for p in plugins)
    status = "ok"
    exit_code = 0

    if core.status == "failed":
        status = "failed"
        exit_code = 2
    elif any(p.status == "failed_rollback" for p in plugins):
        status = "failed"
        exit_code = 3
    elif any(p.status.startswith("failed") for p in plugins):
        status = "partial_failure"
        exit_code = 1
    elif options.strict and any(p.status.startswith("skipped") for p in plugins):
        status = "partial_failure"
        exit_code = 1

    result = SyncRunResult(
        status=status,
        hermes_home=str(home),
        restart_required=restart_required,
        core=core,
        plugins=plugins,
        summary=summary,
    )
    return result, exit_code


def _format_check(name: str, check: CheckResult) -> list[str]:
    lines = [f"    {name}: {check.status}"]
    if check.exit_code is not None:
        lines[-1] += f" (exit {check.exit_code})"
    if check.command:
        lines.append(f"      cmd: {' '.join(check.command)}")
    if check.stdout_tail:
        lines.append(f"      stdout: {check.stdout_tail}")
    if check.stderr_tail:
        lines.append(f"      stderr: {check.stderr_tail}")
    return lines


def render_human(result: SyncRunResult) -> str:
    lines: list[str] = ["hermes-plugin-sync", ""]
    lines.append(f"Hermes home: {result.hermes_home}")
    lines.append("")

    lines.append("Core:")
    lines.append(f"  status: {result.core.status}")
    if result.core.exit_code is not None:
        lines.append(f"  exit: {result.core.exit_code}")
    if result.core.details:
        lines.append(f"  details: {result.core.details}")
    lines.append("")

    lines.append("Plugins:")
    if not result.plugins:
        lines.append("  (none discovered)")
    for plugin in result.plugins:
        lines.append(f"  {plugin.name}:")
        lines.append(f"    status: {plugin.status}")
        if plugin.branch is not None:
            lines.append(f"    branch: {plugin.branch}")
        if plugin.old_commit:
            lines.append(f"    old: {plugin.old_commit}")
        if plugin.new_commit:
            lines.append(f"    new: {plugin.new_commit}")
        if plugin.attempted_commit and plugin.status.startswith("failed"):
            lines.append(f"    attempted: {plugin.attempted_commit}")
        if plugin.reason:
            lines.append(f"    reason: {plugin.reason}")
        if plugin.failed_step:
            lines.append(f"    failed_step: {plugin.failed_step}")
        if plugin.rollback_status:
            lines.append(f"    rollback: {plugin.rollback_status}")
        for check_name, check in plugin.checks.items():
            lines.extend(_format_check(check_name, check))
        lines.append("")

    lines.append("Summary:")
    lines.append(f"  total: {result.summary.total}")
    lines.append(f"  updated: {result.summary.updated}")
    lines.append(f"  unchanged: {result.summary.unchanged}")
    lines.append(f"  skipped: {result.summary.skipped}")
    lines.append(f"  failed: {result.summary.failed}")
    lines.append(f"  rolled_back: {result.summary.rolled_back}")
    lines.append("")
    lines.append(f"Restart required: {'yes' if result.restart_required else 'no'}")
    lines.append("")
    return "\n".join(lines)


def render_json(result: SyncRunResult) -> str:
    return json.dumps(_jsonable(result), indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes-plugin-sync",
        description="Update Hermes core and installed Git-backed Hermes plugins from origin/main.",
    )
    parser.add_argument("--hermes-home", type=Path, default=None, help="Hermes home directory (default: ~/.hermes or $HERMES_HOME)")
    parser.add_argument("--plugins", type=str, default=None, help="Comma-separated plugin names to sync (default: all installed plugins)")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Print JSON output")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without mutating anything")
    parser.add_argument("--verbose", action="store_true", help="Reserved for future richer diagnostics")
    parser.add_argument("--skip-core", action="store_true", help="Skip the hermes-plugin-core update step")
    parser.add_argument("--strict", action="store_true", help="Treat skipped repos as failures")
    parser.add_argument("--no-install", dest="run_install", action="store_false", help="Skip plugin install step")
    parser.add_argument("--no-test", dest="run_test", action="store_false", help="Skip plugin test step")
    parser.add_argument("--no-audit", dest="run_audit", action="store_false", help="Skip plugin audit step")
    parser.set_defaults(run_install=True, run_test=True, run_audit=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    plugins = None
    if args.plugins:
        plugins = [item.strip() for item in args.plugins.split(",") if item.strip()]
    result, exit_code = run_sync(
        SyncOptions(
            hermes_home=args.hermes_home,
            plugins=plugins,
            json_output=args.json_output,
            dry_run=args.dry_run,
            verbose=args.verbose,
            skip_core=args.skip_core,
            strict=args.strict,
            run_install=args.run_install,
            run_test=args.run_test,
            run_audit=args.run_audit,
        )
    )
    output = render_json(result) if args.json_output else render_human(result)
    print(output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
