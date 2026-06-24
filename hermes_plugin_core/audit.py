"""
hermes_plugin_core.audit — Plugin compliance checker.

Run all structural and code-quality checks against a plugin repo and report
exactly what's wrong.  Importable as a library or invoked via
``python setup.py audit``.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class AuditStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


@dataclass
class AuditResult:
    status: AuditStatus
    label: str     # short label, e.g. "File structure"
    message: str   # detail message


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Optional[dict]:
    """Return parsed YAML dict, or None on failure."""
    try:
        from ruamel.yaml import YAML  # type: ignore
        y = YAML()
        with path.open("r", encoding="utf-8") as fh:
            return y.load(fh)
    except ImportError:
        pass
    try:
        import yaml  # type: ignore
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except Exception:
        return None
    except ImportError:
        return None


def _parse_ast(path: Path) -> Optional[ast.Module]:
    """Return parsed AST, or None on syntax error."""
    try:
        src = path.read_text(encoding="utf-8")
        return ast.parse(src, filename=str(path))
    except (SyntaxError, OSError):
        return None


def _top_level_function_names(tree: ast.Module) -> list[str]:
    """Return names of top-level async def / def in an AST."""
    names = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
    return names


def _top_level_function_nodes(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return top-level function/async-function AST nodes."""
    return [
        n for n in ast.iter_child_nodes(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _has_call_name(tree: ast.AST, name: str) -> bool:
    """Return True if any Call node has a simple function name == *name*."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == name:
                return True
    return False


def _has_attr_call(tree: ast.AST, obj: str, attr: str) -> bool:
    """Return True if any Call node looks like ``obj.attr(...)``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == attr
                and isinstance(func.value, ast.Name)
                and func.value.id == obj
            ):
                return True
    return False


def _has_nested_call_attr(tree: ast.AST, attr: str) -> bool:
    """Return True if any Call node has an Attribute ``attr`` (any receiver)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == attr:
                return True
    return False


def _func_has_try(fn_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function body contains at least one Try node."""
    for node in ast.walk(fn_node):
        if isinstance(node, (ast.Try, ast.TryStar)):
            return True
    return False


def _func_has_json_dumps(fn_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function body contains a json.dumps(...) call."""
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Call):
            func = node.func
            # json.dumps
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "dumps"
                and isinstance(func.value, ast.Name)
                and func.value.id == "json"
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

REQUIRED_FILES = [
    "__init__.py",
    "tools.py",
    "schemas.py",
    "plugin.yaml",
    "setup.py",
    "setup.sh",
    "SKILL.md",
]

REQUIRED_YAML_KEYS = ["name", "version", "description", "provides_tools"]


def _check_required_files(repo_dir: Path) -> AuditResult:
    """Check 1: Required files present."""
    missing = [f for f in REQUIRED_FILES if not (repo_dir / f).exists()]
    if missing:
        return AuditResult(
            AuditStatus.FAIL,
            "File structure",
            f"missing: {', '.join(missing)}",
        )
    return AuditResult(
        AuditStatus.PASS,
        "File structure",
        f"all {len(REQUIRED_FILES)} required files present",
    )


def _check_plugin_yaml(repo_dir: Path) -> list[AuditResult]:
    """Check 2: plugin.yaml valid."""
    results: list[AuditResult] = []
    yaml_path = repo_dir / "plugin.yaml"
    if not yaml_path.exists():
        results.append(AuditResult(AuditStatus.FAIL, "plugin.yaml", "file not found"))
        return results

    data = _load_yaml(yaml_path)
    if data is None:
        results.append(AuditResult(AuditStatus.FAIL, "plugin.yaml", "failed to parse YAML"))
        return results

    missing_keys = [k for k in REQUIRED_YAML_KEYS if k not in data]
    if missing_keys:
        results.append(AuditResult(
            AuditStatus.FAIL,
            "plugin.yaml",
            f"missing required keys: {', '.join(missing_keys)}",
        ))
        return results

    provides_tools = data.get("provides_tools")
    if not provides_tools or not isinstance(provides_tools, list) or len(provides_tools) == 0:
        results.append(AuditResult(
            AuditStatus.FAIL,
            "plugin.yaml",
            "provides_tools is empty or not a list",
        ))
        return results

    results.append(AuditResult(
        AuditStatus.PASS,
        "plugin.yaml",
        f"valid — {len(provides_tools)} tools declared",
    ))

    if "requires_env" not in data:
        results.append(AuditResult(
            AuditStatus.WARN,
            "plugin.yaml",
            "requires_env key absent (optional but recommended)",
        ))

    return results


def _check_setup_sh(repo_dir: Path) -> AuditResult:
    """Check 3: setup.sh correct."""
    sh_path = repo_dir / "setup.sh"
    if not sh_path.exists():
        return AuditResult(AuditStatus.FAIL, "setup.sh", "file not found")
    try:
        content = sh_path.read_text(encoding="utf-8")
    except OSError as exc:
        return AuditResult(AuditStatus.FAIL, "setup.sh", f"read error: {exc}")

    has_python = "python3" in content or "python" in content
    has_setup = "setup.py" in content
    if has_python and has_setup:
        return AuditResult(AuditStatus.PASS, "setup.sh", "delegates to setup.py correctly")
    return AuditResult(
        AuditStatus.WARN,
        "setup.sh",
        "does not delegate to setup.py (may be old-style)",
    )


def _check_setup_py_uses_core(repo_dir: Path) -> AuditResult:
    """Check 4: setup.py uses hermes-plugin-core."""
    py_path = repo_dir / "setup.py"
    if not py_path.exists():
        return AuditResult(AuditStatus.FAIL, "setup.py", "file not found")
    try:
        content = py_path.read_text(encoding="utf-8")
    except OSError as exc:
        return AuditResult(AuditStatus.FAIL, "setup.py", f"read error: {exc}")

    if "hermes_plugin_core" in content:
        return AuditResult(AuditStatus.PASS, "setup.py", "uses hermes_plugin_core")
    return AuditResult(
        AuditStatus.WARN,
        "setup.py",
        "hermes_plugin_core not found — pre-migration plugin",
    )


def _check_init_setup_logging(repo_dir: Path) -> AuditResult:
    """Check 5: __init__.py calls setup_logging."""
    init_path = repo_dir / "__init__.py"
    if not init_path.exists():
        return AuditResult(AuditStatus.FAIL, "__init__.py", "file not found")
    tree = _parse_ast(init_path)
    if tree is None:
        return AuditResult(AuditStatus.FAIL, "__init__.py", "syntax error — could not parse")

    if _has_call_name(tree, "setup_logging"):
        return AuditResult(AuditStatus.PASS, "__init__.py", "setup_logging() call found")
    return AuditResult(AuditStatus.FAIL, "__init__.py", "missing setup_logging() call")


def _check_init_register(repo_dir: Path) -> AuditResult:
    """Check 6: __init__.py calls ctx.register_skill or ctx.register_tools."""
    init_path = repo_dir / "__init__.py"
    if not init_path.exists():
        return AuditResult(AuditStatus.FAIL, "__init__.py (register)", "file not found")
    tree = _parse_ast(init_path)
    if tree is None:
        return AuditResult(AuditStatus.FAIL, "__init__.py (register)", "syntax error")

    found = _has_attr_call(tree, "ctx", "register_skill") or _has_attr_call(tree, "ctx", "register_tools")
    if found:
        return AuditResult(
            AuditStatus.PASS,
            "__init__.py (register)",
            "ctx.register_skill / ctx.register_tools call found",
        )
    return AuditResult(
        AuditStatus.WARN,
        "__init__.py (register)",
        "ctx.register_skill / ctx.register_tools not found (may be simpler plugin)",
    )


def _check_tool_registry_crossref(repo_dir: Path) -> list[AuditResult]:
    """Check 7: Tool registry cross-reference."""
    results: list[AuditResult] = []

    yaml_path = repo_dir / "plugin.yaml"
    tools_path = repo_dir / "tools.py"

    if not yaml_path.exists() or not tools_path.exists():
        results.append(AuditResult(
            AuditStatus.FAIL,
            "tool registry",
            "plugin.yaml or tools.py not found — skipping cross-reference",
        ))
        return results

    data = _load_yaml(yaml_path)
    provides_tools: list[str] = []
    if data and isinstance(data.get("provides_tools"), list):
        provides_tools = list(data["provides_tools"])

    tree = _parse_ast(tools_path)
    if tree is None:
        results.append(AuditResult(
            AuditStatus.FAIL, "tool registry", "tools.py has syntax error — cannot parse"
        ))
        return results

    defined_fns = set(_top_level_function_names(tree))
    declared = set(provides_tools)

    missing_impl = sorted(declared - defined_fns)
    extra_helpers = sorted(defined_fns - declared)

    if missing_impl:
        results.append(AuditResult(
            AuditStatus.FAIL,
            "tool registry",
            f"tools declared in plugin.yaml but missing from tools.py: {', '.join(missing_impl)}",
        ))
    else:
        results.append(AuditResult(
            AuditStatus.PASS,
            "tool registry",
            f"all {len(declared)} declared tools have implementations in tools.py",
        ))

    if extra_helpers:
        results.append(AuditResult(
            AuditStatus.WARN,
            "tool registry",
            f"helpers found in tools.py not in plugin.yaml: {', '.join(extra_helpers)}",
        ))

    return results


def _check_tools_try_except(repo_dir: Path) -> AuditResult:
    """Check 8: Tool handlers have try/except."""
    tools_path = repo_dir / "tools.py"
    if not tools_path.exists():
        return AuditResult(AuditStatus.FAIL, "tools try/except", "tools.py not found")
    tree = _parse_ast(tools_path)
    if tree is None:
        return AuditResult(AuditStatus.FAIL, "tools try/except", "tools.py syntax error")

    fn_nodes = _top_level_function_nodes(tree)
    missing = [fn.name for fn in fn_nodes if not _func_has_try(fn)]

    if missing:
        return AuditResult(
            AuditStatus.FAIL,
            "tools try/except",
            f"missing try/except: {', '.join(missing)}",
        )
    if not fn_nodes:
        return AuditResult(AuditStatus.WARN, "tools try/except", "no functions found in tools.py")
    return AuditResult(
        AuditStatus.PASS,
        "tools try/except",
        f"all {len(fn_nodes)} tool functions have try/except",
    )


def _check_tools_json_dumps(repo_dir: Path) -> AuditResult:
    """Check 9: Tool handlers return json.dumps."""
    tools_path = repo_dir / "tools.py"
    if not tools_path.exists():
        return AuditResult(AuditStatus.FAIL, "tools json.dumps", "tools.py not found")
    tree = _parse_ast(tools_path)
    if tree is None:
        return AuditResult(AuditStatus.FAIL, "tools json.dumps", "tools.py syntax error")

    fn_nodes = _top_level_function_nodes(tree)
    missing = [fn.name for fn in fn_nodes if not _func_has_json_dumps(fn)]

    if missing:
        return AuditResult(
            AuditStatus.FAIL,
            "tools json.dumps",
            f"missing json.dumps return: {', '.join(missing)}",
        )
    if not fn_nodes:
        return AuditResult(AuditStatus.WARN, "tools json.dumps", "no functions found in tools.py")
    return AuditResult(
        AuditStatus.PASS,
        "tools json.dumps",
        f"all {len(fn_nodes)} tool functions use json.dumps",
    )


def _check_skill_md_content(repo_dir: Path) -> list[AuditResult]:
    """Check 10: SKILL.md has required frontmatter keys and required sections."""
    results: list[AuditResult] = []
    skill_path = repo_dir / "SKILL.md"
    if not skill_path.exists():
        # Already caught by _check_required_files; skip to avoid duplicate FAIL
        return results

    try:
        content = skill_path.read_text(encoding="utf-8")
    except OSError as exc:
        results.append(AuditResult(AuditStatus.FAIL, "SKILL.md content", f"read error: {exc}"))
        return results

    # ---- frontmatter ----
    REQUIRED_FRONTMATTER = ["name:", "description:", "triggers:"]
    TEMPLATE_PLACEHOLDER = "<plugin-name>"

    if not content.startswith("---"):
        results.append(AuditResult(
            AuditStatus.FAIL,
            "SKILL.md frontmatter",
            "missing YAML frontmatter (file must start with ---)",
        ))
    else:
        missing_fm = [k for k in REQUIRED_FRONTMATTER if k not in content]
        if missing_fm:
            results.append(AuditResult(
                AuditStatus.FAIL,
                "SKILL.md frontmatter",
                f"missing required frontmatter keys: {', '.join(missing_fm)}",
            ))
        elif TEMPLATE_PLACEHOLDER in content:
            results.append(AuditResult(
                AuditStatus.FAIL,
                "SKILL.md frontmatter",
                f"template placeholder '{TEMPLATE_PLACEHOLDER}' not replaced — SKILL.md is unfilled",
            ))
        else:
            results.append(AuditResult(
                AuditStatus.PASS,
                "SKILL.md frontmatter",
                "name, description, triggers present and filled in",
            ))

    # ---- required sections ----
    REQUIRED_SECTIONS = ["Common Patterns", "Pitfalls"]
    missing_sections = [s for s in REQUIRED_SECTIONS if s not in content]
    if missing_sections:
        results.append(AuditResult(
            AuditStatus.FAIL,
            "SKILL.md sections",
            f"missing required sections: {', '.join(missing_sections)}",
        ))
    else:
        results.append(AuditResult(
            AuditStatus.PASS,
            "SKILL.md sections",
            "Common Patterns and Pitfalls sections present",
        ))

    return results


def _check_tools_logger(repo_dir: Path) -> AuditResult:
    """Check 11: tools.py uses getLogger, not setup_logging."""
    tools_path = repo_dir / "tools.py"
    if not tools_path.exists():
        return AuditResult(AuditStatus.FAIL, "tools logger", "tools.py not found")
    tree = _parse_ast(tools_path)
    if tree is None:
        return AuditResult(AuditStatus.FAIL, "tools logger", "tools.py syntax error")

    # Check if setup_logging is called anywhere in tools.py (should NOT be)
    if _has_call_name(tree, "setup_logging"):
        return AuditResult(
            AuditStatus.FAIL,
            "tools logger",
            "setup_logging() called in tools.py — should only be in __init__.py",
        )

    # Check for logging.getLogger at module level (top-level assignments)
    has_get_logger = False
    for node in ast.iter_child_nodes(tree):
        # Look for: logger = logging.getLogger(...)  at module level
        if isinstance(node, ast.Assign):
            for subnode in ast.walk(node):
                if (
                    isinstance(subnode, ast.Call)
                    and isinstance(subnode.func, ast.Attribute)
                    and subnode.func.attr == "getLogger"
                ):
                    has_get_logger = True
                    break
        # Also accept: LOG = logging.getLogger(...) via AugAssign or AnnAssign
        if isinstance(node, ast.AnnAssign) and node.value:
            for subnode in ast.walk(node.value):
                if (
                    isinstance(subnode, ast.Call)
                    and isinstance(subnode.func, ast.Attribute)
                    and subnode.func.attr == "getLogger"
                ):
                    has_get_logger = True
                    break

    if has_get_logger:
        return AuditResult(
            AuditStatus.PASS,
            "tools logger",
            "logging.getLogger found at module level",
        )
    return AuditResult(
        AuditStatus.WARN,
        "tools logger",
        "no module-level logger found in tools.py",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_audit(repo_dir: Path, plugin_key: Optional[str] = None) -> list[AuditResult]:
    """Run all checks against a plugin repo. Returns list of AuditResult."""
    repo_dir = Path(repo_dir).resolve()
    results: list[AuditResult] = []

    # Check 1
    results.append(_check_required_files(repo_dir))
    # Check 2
    results.extend(_check_plugin_yaml(repo_dir))
    # Check 3
    results.append(_check_setup_sh(repo_dir))
    # Check 4
    results.append(_check_setup_py_uses_core(repo_dir))
    # Check 5
    results.append(_check_init_setup_logging(repo_dir))
    # Check 6
    results.append(_check_init_register(repo_dir))
    # Check 7
    results.extend(_check_tool_registry_crossref(repo_dir))
    # Check 8
    results.append(_check_tools_try_except(repo_dir))
    # Check 9
    results.append(_check_tools_json_dumps(repo_dir))
    # Check 10
    results.extend(_check_skill_md_content(repo_dir))
    # Check 11
    results.append(_check_tools_logger(repo_dir))

    return results


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

_ICONS = {
    AuditStatus.PASS: "✓",
    AuditStatus.FAIL: "✗",
    AuditStatus.WARN: "⚠",
}


def print_audit_report(plugin_key: str, results: list[AuditResult]) -> bool:
    """
    Print a formatted audit report. Returns True if no FAILs (audit passed).

    Example output::

        hermes-plugin-jira audit
        ─────────────────────────
        ✓ File structure        all 7 required files present
        ✓ plugin.yaml           valid — 16 tools declared
        ✗ __init__.py           missing setup_logging() call
        ⚠ tool registry         helpers found in tools.py not in plugin.yaml: _build_headers
        ─────────────────────────
        1 error, 1 warning — plugin needs attention
    """
    title = f"hermes-plugin-{plugin_key} audit"
    separator = "─" * max(len(title), 50)

    print(title)
    print(separator)

    label_width = max((len(r.label) for r in results), default=20) + 2

    for r in results:
        icon = _ICONS[r.status]
        print(f"  {icon} {r.label:<{label_width}} {r.message}")

    print(separator)

    num_fail = sum(1 for r in results if r.status == AuditStatus.FAIL)
    num_warn = sum(1 for r in results if r.status == AuditStatus.WARN)

    if num_fail == 0 and num_warn == 0:
        print("All checks passed ✓")
    else:
        parts: list[str] = []
        if num_fail:
            parts.append(f"{num_fail} error{'s' if num_fail != 1 else ''}")
        if num_warn:
            parts.append(f"{num_warn} warning{'s' if num_warn != 1 else ''}")
        verdict = "plugin needs attention" if num_fail else "review warnings"
        print(", ".join(parts) + f" — {verdict}")

    return num_fail == 0
