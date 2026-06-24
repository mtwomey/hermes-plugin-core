"""
hermes_plugin_core.config — config.yaml management for Hermes plugins.

CRITICAL: Always use ruamel.yaml, never PyYAML (import yaml).
PyYAML strips comments and reorders keys, corrupting the user's config.yaml.

All mutating functions accept an optional `home: Path | None = None` parameter
for testability — pass a tmp_path in tests to avoid touching ~/.hermes/config.yaml.

Usage:
    from hermes_plugin_core.config import (
        hermes_home, load_yaml, save_yaml,
        plugin_enable, plugin_disable, plugin_is_enabled,
        get_log_level, set_log_level,
    )

    # Enable a plugin
    plugin_enable("jira")

    # Check if enabled
    if plugin_is_enabled("jira"):
        ...

    # Read/set log level
    level = get_log_level("jira")        # returns "WARNING" by default
    set_log_level("jira", "DEBUG")       # sets plugins.config.jira.log_level
    set_log_level("jira", None)          # removes the key (back to default WARNING)
"""

from __future__ import annotations

import io
import os
from pathlib import Path


def hermes_home() -> Path:
    """Return the Hermes home directory (~/.hermes by default, or $HERMES_HOME)."""
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def config_yaml_path(home: Path | None = None) -> Path:
    """Return the path to config.yaml."""
    return (home or hermes_home()) / "config.yaml"


def load_yaml(home: Path | None = None):
    """Load config.yaml. Returns (yaml_instance, data_dict). Safe if file doesn't exist."""
    from ruamel.yaml import YAML, CommentedMap
    yaml = YAML()
    yaml.preserve_quotes = True
    cfg_path = config_yaml_path(home)
    if not cfg_path.exists():
        return yaml, CommentedMap()
    with open(cfg_path) as f:
        data = yaml.load(f) or CommentedMap()
    return yaml, data


def save_yaml(yaml, data, home: Path | None = None) -> None:
    """Save config.yaml, preserving comments and key order."""
    cfg_path = config_yaml_path(home)
    buf = io.StringIO()
    yaml.dump(data, buf)
    cfg_path.write_text(buf.getvalue())


def plugin_enable(plugin_key: str, home: Path | None = None) -> None:
    """Add plugin_key to plugins.enabled in config.yaml if not already present."""
    from ruamel.yaml import CommentedMap, CommentedSeq
    yaml, data = load_yaml(home)
    if "plugins" not in data or data["plugins"] is None:
        data["plugins"] = CommentedMap()
    if "enabled" not in data["plugins"] or data["plugins"]["enabled"] is None:
        data["plugins"]["enabled"] = CommentedSeq()
    enabled = data["plugins"]["enabled"]
    if plugin_key not in enabled:
        enabled.append(plugin_key)
        save_yaml(yaml, data, home)


def plugin_disable(plugin_key: str, home: Path | None = None) -> None:
    """Remove plugin_key from plugins.enabled in config.yaml."""
    yaml, data = load_yaml(home)
    plugins = data.get("plugins") or {}
    enabled = plugins.get("enabled") or []
    if plugin_key in enabled:
        enabled.remove(plugin_key)
        save_yaml(yaml, data, home)


def plugin_is_enabled(plugin_key: str, home: Path | None = None) -> bool:
    """Return True if plugin_key is in plugins.enabled."""
    _, data = load_yaml(home)
    plugins = data.get("plugins") or {}
    enabled = plugins.get("enabled") or []
    return plugin_key in enabled


def get_log_level(plugin_key: str, home: Path | None = None) -> str:
    """Read plugins.config.<plugin_key>.log_level. Returns 'WARNING' if not set."""
    _, data = load_yaml(home)
    plugins = data.get("plugins") or {}
    config = plugins.get("config") or {}
    plugin_cfg = config.get(plugin_key) or {}
    return plugin_cfg.get("log_level") or "WARNING"


def set_log_level(plugin_key: str, level: str | None, home: Path | None = None) -> None:
    """Set plugins.config.<plugin_key>.log_level. Pass None to remove (reset to WARNING)."""
    from ruamel.yaml import CommentedMap
    yaml, data = load_yaml(home)
    if "plugins" not in data or data["plugins"] is None:
        data["plugins"] = CommentedMap()
    if "config" not in data["plugins"] or data["plugins"]["config"] is None:
        data["plugins"]["config"] = CommentedMap()
    if plugin_key not in data["plugins"]["config"] or data["plugins"]["config"][plugin_key] is None:
        data["plugins"]["config"][plugin_key] = CommentedMap()
    if level is None:
        if "log_level" in data["plugins"]["config"][plugin_key]:
            del data["plugins"]["config"][plugin_key]["log_level"]
    else:
        data["plugins"]["config"][plugin_key]["log_level"] = level
    save_yaml(yaml, data, home)
