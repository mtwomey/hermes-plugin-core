"""
hermes_plugin_core.scaffold — generate a new Hermes plugin from a template.

Usage:
    python -m hermes_plugin_core scaffold <plugin-name>
    hermes-plugin-scaffold <plugin-name>   # via installed entry point

Example:
    python -m hermes_plugin_core scaffold my-api
    # Creates: ~/Git_Repos/hermes-plugin-my-api/
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Maps: (template_filename, output_filename, make_executable)
TEMPLATE_MAP = [
    ("plugin_yaml.yaml",     "plugin.yaml",           False),
    ("__init___py.txt",      "__init__.py",           False),
    ("tools_py.txt",         "tools.py",              False),
    ("schemas_py.txt",       "schemas.py",            False),
    ("setup_py.txt",         "setup.py",              False),
    ("setup_sh.txt",         "setup.sh",              True),
    ("SKILL_md.txt",         "SKILL.md",              False),
    ("plugin_tests_py.txt",  "tests/plugin_tests.py", False),
]


def _render(template_text: str, plugin_name: str, plugin_key: str) -> str:
    """Replace template placeholders."""
    return (
        template_text
        .replace("{{PLUGIN_NAME}}", plugin_name)
        .replace("{{PLUGIN_KEY}}", plugin_key)
        .replace("{{PLUGIN_SERVICE}}", f"hermes-{plugin_name}")
    )


def scaffold(plugin_name: str, output_dir: Path | None = None) -> Path:
    """
    Generate a new plugin repo for the given plugin_name.

    plugin_name: e.g. "my-api" (hyphens OK)
    output_dir: where to create the repo (default: ~/Git_Repos/)

    Returns the created repo path.
    """
    plugin_key = plugin_name.replace("-", "_")
    repo_name = f"hermes-plugin-{plugin_name}"
    base_dir = output_dir or (Path.home() / "Git_Repos")
    repo_dir = base_dir / repo_name

    if repo_dir.exists():
        print(f"Error: {repo_dir} already exists.")
        sys.exit(1)

    print(f"Scaffolding {repo_name}...")
    print("─" * 45)

    # Create directories
    repo_dir.mkdir(parents=True)
    (repo_dir / "tests").mkdir()

    # Render and write each template
    for tmpl_name, out_name, executable in TEMPLATE_MAP:
        tmpl_path = TEMPLATES_DIR / tmpl_name
        out_path = repo_dir / out_name
        tmpl_text = tmpl_path.read_text()
        rendered = _render(tmpl_text, plugin_name, plugin_key)
        out_path.write_text(rendered)
        if executable:
            out_path.chmod(0o755)
        print(f"✓ {out_name}")

    # git init + initial commit
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    # Write .gitignore
    (repo_dir / ".gitignore").write_text(
        "__pycache__/\n*.py[cod]\n*.egg-info/\n.eggs/\ndist/\nbuild/\n"
    )
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"chore: scaffold hermes-plugin-{plugin_name}"],
        cwd=repo_dir, check=True, capture_output=True
    )
    print("✓ git init + initial commit")
    print("─" * 45)
    print("Next steps:")
    print(f"  1. Fill in PluginConfig in {repo_dir}/setup.py")
    print( "  2. Write your tool schemas in schemas.py")
    print( "  3. Implement your tools in tools.py")
    print(f"  4. cd {repo_dir} && ./setup.sh install")
    print( "  5. Restart Hermes")
    print( "  6. python setup.py audit   ← verify compliance")
    print( "  7. python setup.py test    ← verify tools work")
    return repo_dir


def main():
    parser = argparse.ArgumentParser(
        prog="hermes-plugin-scaffold",
        description="Generate a new Hermes plugin from a template.",
    )
    parser.add_argument("plugin_name", help="Plugin name, e.g. 'my-api' (hyphens OK)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to create the repo (default: ~/Git_Repos/)",
    )
    args = parser.parse_args()
    scaffold(args.plugin_name, args.output_dir)


if __name__ == "__main__":
    main()
