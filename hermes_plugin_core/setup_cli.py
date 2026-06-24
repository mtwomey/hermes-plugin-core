"""
hermes_plugin_core.setup_cli — Parameterized setup CLI for Hermes native plugins.

Replaces the ~400-line setup.py in each plugin with ~30 lines of constants:

    from pathlib import Path
    from hermes_plugin_core import SetupCLI, PluginConfig

    cfg = PluginConfig(
        plugin_key="jira",
        service="hermes-jira",
        repo_dir=Path(__file__).parent.resolve(),
        keys=["api_token", "base_url"],
        cred_prompts={
            "api_token": ("Jira API token", "", True),
            "base_url":  ("Jira base URL (e.g. https://yourco.atlassian.net)", "", False),
        },
        requirements=["requests"],
    )

    if __name__ == "__main__":
        SetupCLI(cfg).run()
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from hermes_plugin_core.config import (
    get_log_level,
    hermes_home,
    plugin_disable,
    plugin_enable,
    plugin_is_enabled,
    set_log_level,
)
from hermes_plugin_core.keychain import cred_delete, cred_get, cred_set
from hermes_plugin_core.testing import run_plugin_tests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HERMES_VENV_PYTHON = Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "python3"
HERMES_PLUGINS_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "plugins"
HERMES_SKILLS_DIR  = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "skills"

OK   = "✓"
FAIL = "✗"
WARN = "⚠"


# ---------------------------------------------------------------------------
# PluginConfig dataclass
# ---------------------------------------------------------------------------
@dataclass
class PluginConfig:
    """All the plugin-specific constants that parameterise SetupCLI."""

    plugin_key: str
    """Short identifier used for the symlink name, config key, and log key (e.g. "jira")."""

    service: str
    """Keychain service name (e.g. "hermes-jira")."""

    repo_dir: Path
    """Absolute path to the plugin repository root. Pass ``Path(__file__).parent.resolve()``."""

    keys: list[str]
    """Credential key names stored in Keychain."""

    cred_prompts: dict[str, tuple[str, str, bool]]
    """Mapping of key → (label, default_value, is_secret)."""

    requirements: list[str] = field(default_factory=list)
    """pip packages to install into the Hermes venv."""

    has_skill_stub: bool = False
    """Whether the plugin ships a ``skill-stub/`` directory."""

    skill_stub_category: str = "email"
    """Subdirectory under ``~/.hermes/skills/`` for the skill-stub symlink."""


# ---------------------------------------------------------------------------
# SetupCLI
# ---------------------------------------------------------------------------
class SetupCLI:
    """Parameterised setup CLI — all subcommands derived from a PluginConfig."""

    def __init__(self, config: PluginConfig) -> None:
        self.config = config
        cfg = config
        self._hermes_home = hermes_home()
        self._config_yaml = self._hermes_home / "config.yaml"
        self._plugin_link = HERMES_PLUGINS_DIR / cfg.plugin_key
        if cfg.has_skill_stub:
            self._skill_stub_src  = cfg.repo_dir / "skill-stub"
            self._skill_stub_link = HERMES_SKILLS_DIR / cfg.skill_stub_category / cfg.plugin_key
        else:
            self._skill_stub_src  = None
            self._skill_stub_link = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _make_symlink(self, link: Path, target: Path, label: str) -> None:
        """Create a symlink, handling existing/wrong-target cases."""
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            if link.resolve() == target.resolve():
                print(f"  {OK} {label}: Already linked correctly — skipping")
                return
            link.unlink()
        elif link.exists():
            print(f"  {FAIL} {label}: Path exists but is not a symlink: {link}")
            print("  Remove it manually and re-run.")
            sys.exit(1)
        link.symlink_to(target)
        print(f"  {OK} {label}: {link} → {target}")

    def _remove_symlink(self, link: Path, label: str) -> None:
        """Remove a symlink, warning if not found."""
        if link.is_symlink():
            link.unlink()
            print(f"  {OK} {label}: Symlink removed: {link}")
        else:
            print(f"  {WARN} {label}: Symlink not found — skipping")

    def _install_requirements(self) -> None:
        """Install pip requirements into the Hermes venv."""
        reqs = self.config.requirements
        if not reqs:
            return
        print("\n[0] Python dependencies")
        r = subprocess.run(
            [str(HERMES_VENV_PYTHON), "-m", "pip", "install", "--quiet", *reqs],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            print(f"  {FAIL} pip install failed:\n{r.stderr}")
            sys.exit(1)
        print(f"  {OK} All dependencies installed ({', '.join(reqs)})")

    def _cred_status(self) -> dict[str, str]:
        """Return {key: 'keychain'|'missing'} for each configured credential key."""
        cfg = self.config
        return {k: ("keychain" if cred_get(cfg.service, k) else "missing") for k in cfg.keys}

    def _prompt_credentials(self, yes: bool = False) -> None:
        """Interactively prompt for credentials and store them in Keychain."""
        cfg = self.config
        print(f"\n{cfg.plugin_key} credential setup")
        print("=" * 50)
        print("Tip: if you don't have these handy, just press Enter to skip and")
        print(f"run 'python setup.py credentials configure' later.\n")

        for key, (label, default, is_secret) in cfg.cred_prompts.items():
            existing = cred_get(cfg.service, key)
            if existing:
                if yes:
                    print(f"  {key}: already set, skipping")
                    continue
                ans = input(f"  {key} already set. Update? [y/N] ").strip().lower()
                if ans != "y":
                    continue

            if yes and not existing:
                print(f"  {WARN} {key} not set — run 'python setup.py credentials configure' interactively")
                continue

            prompt_text = f"  {label}"
            if default:
                prompt_text += f" [{default}]"
            prompt_text += ": "

            if is_secret:
                import getpass
                value = getpass.getpass(prompt_text).strip()
            else:
                value = input(prompt_text).strip()

            if not value and default:
                value = default

            if value:
                cred_set(cfg.service, key, value)
                print(f"  {OK} {key} stored")
            else:
                print(f"  {WARN} {key} skipped (empty)")

    # ------------------------------------------------------------------
    # Subcommands
    # ------------------------------------------------------------------

    def cmd_install(self, args) -> None:
        cfg = self.config
        print()
        print("=" * 52)
        print(f"  hermes-plugin-{cfg.plugin_key} install")
        print("=" * 52)

        self._install_requirements()

        print("\n[1/3] Symlinks")
        self._make_symlink(self._plugin_link, cfg.repo_dir, "plugin symlink")
        if cfg.has_skill_stub:
            self._make_symlink(self._skill_stub_link, self._skill_stub_src, "skill stub symlink")

        print("\n[2/3] Plugin enable (config.yaml)")
        plugin_enable(cfg.plugin_key)
        print(f"  {OK} '{cfg.plugin_key}' added to / already in plugins.enabled")

        print("\n[3/3] Credentials")
        missing = [k for k in cfg.keys if not cred_get(cfg.service, k)]
        if not missing:
            print(f"  {OK} All credentials present in Keychain")
        else:
            print(f"  {WARN} Missing: {', '.join(missing)}")
            if args.yes:
                print("   Run: python setup.py credentials configure")
            else:
                self._prompt_credentials(yes=False)

        print(f"\n{OK} Install complete. Restart Hermes to activate.\n")

    def cmd_uninstall(self, args) -> None:
        cfg = self.config
        print()
        print("=" * 52)
        print(f"  hermes-plugin-{cfg.plugin_key} uninstall")
        print("=" * 52)

        print("\n[1/3] Symlinks")
        self._remove_symlink(self._plugin_link, "plugin symlink")
        if cfg.has_skill_stub:
            self._remove_symlink(self._skill_stub_link, "skill stub symlink")

        print("\n[2/3] Plugin disable (config.yaml)")
        plugin_disable(cfg.plugin_key)
        print(f"  {OK} '{cfg.plugin_key}' removed from / not in plugins.enabled")

        print("\n[3/3] Credentials")
        has_creds = any(cred_get(cfg.service, k) for k in cfg.keys)
        if has_creds:
            if args.yes:
                ans = "n"
            else:
                ans = input("  Delete credentials from Keychain? [y/N] ").strip().lower()
            if ans == "y":
                for k in cfg.keys:
                    cred_delete(cfg.service, k)
                print(f"  {OK} Credentials deleted from Keychain")
            else:
                print("  Credentials kept in Keychain")
        else:
            print(f"  {WARN} No credentials in Keychain — skipping")

        print(f"\n{OK} Uninstall complete. Restart Hermes.\n")

    def cmd_status(self, args) -> None:
        cfg = self.config
        print()
        print("=" * 52)
        print(f"  hermes-plugin-{cfg.plugin_key} status")
        print("=" * 52)

        print("\nHermes:")
        print(f"  venv Python : {'present' if HERMES_VENV_PYTHON.exists() else FAIL + ' NOT FOUND'}")
        print(f"  config.yaml : {'present' if self._config_yaml.exists() else FAIL + ' NOT FOUND'}")

        print(f"\nPlugin symlink (plugins/{cfg.plugin_key} → repo):")
        if self._plugin_link.is_symlink() and self._plugin_link.resolve() == cfg.repo_dir.resolve():
            print(f"  {OK} {self._plugin_link} → {cfg.repo_dir}")
        elif self._plugin_link.is_symlink():
            print(f"  {WARN} Symlink points elsewhere: {self._plugin_link.resolve()}")
        else:
            print(f"  {FAIL} Symlink missing: {self._plugin_link}")

        if cfg.has_skill_stub:
            print(f"\nSkill stub symlink (skills/{cfg.skill_stub_category}/{cfg.plugin_key} → skill-stub/):")
            if (
                self._skill_stub_link.is_symlink()
                and self._skill_stub_link.resolve() == self._skill_stub_src.resolve()
            ):
                print(f"  {OK} {self._skill_stub_link} → {self._skill_stub_src}")
            elif self._skill_stub_link.is_symlink():
                print(f"  {WARN} Symlink points elsewhere: {self._skill_stub_link.resolve()}")
            else:
                print(f"  {FAIL} Symlink missing: {self._skill_stub_link}")

        print("\nPlugin enabled (config.yaml):")
        if plugin_is_enabled(cfg.plugin_key):
            print(f"  {OK} '{cfg.plugin_key}' present in plugins.enabled")
        else:
            print(f"  {FAIL} '{cfg.plugin_key}' missing from plugins.enabled")

        print(f"\nCredentials (Keychain service: {cfg.service}):")
        for k, state in self._cred_status().items():
            icon = OK if state != "missing" else FAIL
            print(f"  {icon} {k:<20} {state}")
        print()

    def cmd_credentials(self, args) -> None:
        cfg = self.config
        action = getattr(args, "cred_action", None)
        if action == "configure":
            self._prompt_credentials(yes=args.yes)
        elif action == "delete":
            if not args.yes:
                ans = input(f"Delete all credentials for service '{cfg.service}'? [y/N] ").strip().lower()
            else:
                ans = "y"
            if ans == "y":
                for k in cfg.keys:
                    cred_delete(cfg.service, k)
                print(f"{OK} All credentials deleted")
            else:
                print("Aborted.")
        else:
            print(f"\nCredentials (Keychain service: {cfg.service}):")
            for k, state in self._cred_status().items():
                icon = OK if state != "missing" else FAIL
                print(f"  {icon} {k:<20} {state}")
            print()

    def cmd_test(self, args) -> None:
        """Run plugin smoke tests via tests/plugin_tests.py."""
        cfg = self.config
        plugin_name = f"hermes-plugin-{cfg.plugin_key}"
        passed = run_plugin_tests(cfg.repo_dir, plugin_name)
        if not passed:
            sys.exit(1)

    def cmd_log(self, args) -> None:
        """Enable, disable, or show the current log level stored in config.yaml."""
        cfg = self.config
        action = getattr(args, "log_action", "status")

        if not plugin_is_enabled(cfg.plugin_key):
            print(f"{FAIL} Plugin '{cfg.plugin_key}' is not enabled — run 'python setup.py install' first")
            sys.exit(1)

        if action == "status":
            level = get_log_level(cfg.plugin_key)
            print(f"\nLog level for plugins.config.{cfg.plugin_key}: {level}")
            log_file = self._hermes_home / "logs" / f"{cfg.plugin_key}.log"
            if log_file.exists():
                print(f"Log file: {log_file}  ({log_file.stat().st_size // 1024} KB)")
            else:
                print(f"Log file: {log_file}  (not yet created)")

        elif action == "debug":
            set_log_level(cfg.plugin_key, "DEBUG")
            print(f"{OK} Log level set to DEBUG. Restart Hermes to apply.")
            print(f"   tail -f {self._hermes_home}/logs/{cfg.plugin_key}.log")

        elif action == "quiet":
            set_log_level(cfg.plugin_key, None)
            print(f"{OK} Log level reset to WARNING (default). Restart Hermes to apply.")

        else:
            print(f"Unknown log action: {action!r}. Use: debug | quiet | status")
            sys.exit(1)

    def cmd_audit(self, args) -> None:
        """Run the plugin compliance audit."""
        from hermes_plugin_core.audit import run_audit, print_audit_report
        cfg = self.config
        results = run_audit(cfg.repo_dir, cfg.plugin_key)
        passed = print_audit_report(cfg.plugin_key, results)
        if not passed:
            sys.exit(1)

    # ------------------------------------------------------------------
    # Argument parser + dispatch
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Parse CLI arguments and dispatch to the appropriate subcommand."""
        cfg = self.config
        parser = argparse.ArgumentParser(
            prog="setup.py",
            description=f"Install/uninstall/manage hermes-plugin-{cfg.plugin_key}",
        )
        parser.add_argument("--yes", "-y", action="store_true", help="Non-interactive mode")
        sub = parser.add_subparsers(dest="command")

        sub.add_parser("install",   help="Install symlinks, enable plugin, configure credentials")
        sub.add_parser("uninstall", help="Remove symlinks and disable plugin")
        sub.add_parser("status",    help="Show current state of all components")

        creds_p = sub.add_parser("credentials", help="Manage Keychain credentials")
        creds_p.add_argument("cred_action", nargs="?", choices=["configure", "delete"])

        log_p = sub.add_parser("log", help="Manage plugin log level")
        log_p.add_argument(
            "log_action",
            nargs="?",
            choices=["debug", "quiet", "status"],
            default="status",
        )

        sub.add_parser("audit", help="Run plugin audit")
        sub.add_parser("test",  help="Run plugin tests")

        args = parser.parse_args()

        dispatch = {
            "install":     self.cmd_install,
            "uninstall":   self.cmd_uninstall,
            "status":      self.cmd_status,
            "credentials": self.cmd_credentials,
            "log":         self.cmd_log,
            "audit":       self.cmd_audit,
            "test":        self.cmd_test,
        }

        fn = dispatch.get(args.command)
        if fn is None:
            parser.print_help()
            sys.exit(0)
        fn(args)
