"""hermes-plugin-core — shared infrastructure for Hermes native plugins."""
__version__ = "0.1.0"

from hermes_plugin_core.logging import setup_logging
from hermes_plugin_core.keychain import cred_get, cred_set, cred_delete, cred_status, cred_cache_clear
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
from hermes_plugin_core.setup_cli import SetupCLI, PluginConfig
from hermes_plugin_core.testing import TestSuite, expect_ok, run_plugin_tests, load_plugin_tests
from hermes_plugin_core.scaffold import scaffold

__all__ = [
    "__version__",
    # logging
    "setup_logging",
    # keychain
    "cred_get",
    "cred_set",
    "cred_delete",
    "cred_status",
    "cred_cache_clear",
    # config
    "hermes_home",
    "load_yaml",
    "save_yaml",
    "plugin_enable",
    "plugin_disable",
    "plugin_is_enabled",
    "get_log_level",
    "set_log_level",
    # setup_cli
    "SetupCLI",
    "PluginConfig",
    # testing
    "TestSuite",
    "expect_ok",
    "run_plugin_tests",
    "load_plugin_tests",
    # scaffold
    "scaffold",
]
