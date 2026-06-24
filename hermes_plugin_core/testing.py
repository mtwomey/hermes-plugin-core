"""
hermes_plugin_core.testing — lightweight smoke test framework for Hermes plugins.

Design goals:
  - No pytest required — runs standalone via `python setup.py test`
  - Tests call plugin tools directly (not via Hermes) — no restart needed
  - Credentials sourced from Keychain exactly as the plugin does normally
  - Each test is a callable that raises on failure, returns on success
  - Timing per test, clear pass/fail summary

Usage in a plugin's tests/plugin_tests.py:

    from hermes_plugin_core.testing import TestSuite

    def register_tests(suite: TestSuite):
        suite.add("ping", ping_test)
        suite.add("list projects", list_projects_test)

    def ping_test():
        import json, sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from tools import jira_ping
        result = json.loads(jira_ping({}))
        assert "status" in result or "user" in result, f"unexpected ping response: {result}"
"""

from __future__ import annotations

import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class TestCase:
    name: str
    fn: Callable[[], None]


@dataclass
class TestResult:
    name: str
    passed: bool
    duration_ms: float
    error: str | None = None  # None if passed


class TestSuite:
    """
    Lightweight test runner for plugin smoke tests.

    Usage:
        suite = TestSuite("hermes-plugin-jira")
        suite.add("ping", my_ping_test_fn)
        suite.add("list projects", my_list_fn)
        suite.run()  # prints report, returns True if all passed
    """

    def __init__(self, plugin_name: str):
        self.plugin_name = plugin_name
        self._tests: list[TestCase] = []

    def add(self, name: str, fn: Callable[[], None]) -> None:
        """Register a test. fn should raise AssertionError or any Exception on failure."""
        self._tests.append(TestCase(name=name, fn=fn))

    def run(self) -> bool:
        """
        Execute all registered tests. Prints a formatted report.
        Returns True if all tests passed, False if any failed.
        """
        results: list[TestResult] = []

        for tc in self._tests:
            t0 = time.perf_counter()
            try:
                tc.fn()
                elapsed_ms = (time.perf_counter() - t0) * 1000
                results.append(TestResult(name=tc.name, passed=True, duration_ms=elapsed_ms))
            except Exception:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                # Capture last 3 lines of the traceback
                tb_lines = traceback.format_exc().rstrip().splitlines()
                short_tb = "\n".join(tb_lines[-3:])
                exc_type = type(sys.exc_info()[1]).__name__
                exc_msg = str(sys.exc_info()[1])
                error_summary = f"{exc_type}: {exc_msg}" if exc_msg else exc_type
                results.append(
                    TestResult(
                        name=tc.name,
                        passed=False,
                        duration_ms=elapsed_ms,
                        error=f"{error_summary}\n    {short_tb}",
                    )
                )

        # ── Report ──────────────────────────────────────────────────────────
        title = f"{self.plugin_name} tests"
        divider = "─" * max(40, len(title) + 4)

        print(f"\n{title}")
        print(divider)

        for r in results:
            if r.passed:
                print(f"  ✓ {r.name:<30} {r.duration_ms:.0f}ms")
            else:
                # First line: ✗ name   FAILED: short message
                first_error_line = r.error.splitlines()[0] if r.error else "unknown error"
                print(f"  ✗ {r.name:<30} FAILED: {first_error_line}")
                # Remaining lines: indented traceback
                extra_lines = r.error.splitlines()[1:] if r.error else []
                for line in extra_lines:
                    print(f"    {line}")

        print(divider)

        n_passed = sum(1 for r in results if r.passed)
        n_failed = len(results) - n_passed

        parts: list[str] = []
        if n_passed:
            parts.append(f"{n_passed} passed")
        if n_failed:
            parts.append(f"{n_failed} failed")
        if not results:
            parts.append("no tests registered")

        print(", ".join(parts))
        print()

        return n_failed == 0


def expect_ok(result: str | dict) -> str:
    """
    Helper for common assertion: parse JSON result and check there's no 'error' key.
    Returns the result string for optional further assertion.

    Usage:
        def ping_test():
            from tools import jira_ping
            expect_ok(jira_ping({}))
    """
    import json

    if isinstance(result, str):
        data = json.loads(result)
    else:
        data = result
    assert "error" not in data, f"Tool returned error: {data.get('error')}"
    return result if isinstance(result, str) else json.dumps(result)


def load_plugin_tests(plugin_tests_path: Path):
    """
    Dynamically load a plugin's tests/plugin_tests.py module.
    Returns the module so the caller can call module.register_tests(suite).
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("plugin_tests", plugin_tests_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_plugin_tests(repo_dir: Path, plugin_name: str) -> bool:
    """
    Main entry point called by SetupCLI.cmd_test().
    Looks for tests/plugin_tests.py in repo_dir, loads it, runs registered tests.
    Returns True if all passed.
    """
    tests_path = repo_dir / "tests" / "plugin_tests.py"
    if not tests_path.exists():
        print(f"No tests found at {tests_path}")
        print("Create tests/plugin_tests.py with a register_tests(suite) function.")
        return False

    mod = load_plugin_tests(tests_path)
    suite = TestSuite(plugin_name)

    if not hasattr(mod, "register_tests"):
        print("tests/plugin_tests.py must define register_tests(suite: TestSuite)")
        return False

    mod.register_tests(suite)
    return suite.run()
