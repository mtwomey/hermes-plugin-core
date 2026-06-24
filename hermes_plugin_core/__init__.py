"""hermes-plugin-core — shared infrastructure for Hermes native plugins."""
__version__ = "0.1.0"

from hermes_plugin_core.logging import setup_logging
from hermes_plugin_core.keychain import cred_get, cred_set, cred_delete, cred_status, cred_cache_clear

__all__ = [
    "setup_logging",
    "cred_get",
    "cred_set",
    "cred_delete",
    "cred_status",
    "cred_cache_clear",
]
