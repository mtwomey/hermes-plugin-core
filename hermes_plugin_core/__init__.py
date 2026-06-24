"""hermes-plugin-core — shared infrastructure for Hermes native plugins."""
__version__ = "0.1.0"

from hermes_plugin_core.config import (
    hermes_home,
    load_yaml,
    save_yaml,
    plugin_enable,
    plugin_disable,
    plugin_is_enabled,
    get_log_level,
    set_log_level,
)

__all__ = [
    "__version__",
    "hermes_home",
    "load_yaml",
    "save_yaml",
    "plugin_enable",
    "plugin_disable",
    "plugin_is_enabled",
    "get_log_level",
    "set_log_level",
]
