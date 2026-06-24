"""
hermes_plugin_core.keychain — Credential storage via macOS Keychain (keyring).

Policy:
  - keyring only. No security CLI fallback.
  - Cache repeated reads in memory per process — avoids repeated Keychain prompts in loops.
  - All public functions take explicit service + key parameters.

Usage:
    from hermes_plugin_core.keychain import cred_get, cred_set, cred_delete, cred_status

    # Read (cached per process)
    token = cred_get("hermes-jira", "api_token")

    # Write
    cred_set("hermes-jira", "api_token", "my-token")

    # Delete
    cred_delete("hermes-jira", "api_token")

    # Status report for a list of keys
    status = cred_status("hermes-jira", ["jira_url", "username", "api_token"])
    # Returns: {"jira_url": "keychain", "username": "keychain", "api_token": "missing"}
"""

from __future__ import annotations

import keyring
import keyring.errors

# Module-level cache — avoids repeated Keychain prompts per process
_cache: dict[tuple[str, str], str | None] = {}


def cred_get(service: str, key: str) -> str | None:
    """Read a credential from Keychain. Returns None if not set. Cached per process."""
    cache_key = (service, key)
    if cache_key not in _cache:
        val = keyring.get_password(service, key)
        _cache[cache_key] = val or None
    return _cache[cache_key]


def cred_set(service: str, key: str, value: str) -> None:
    """Store a credential in Keychain and update the in-process cache."""
    keyring.set_password(service, key, value)
    _cache[(service, key)] = value


def cred_delete(service: str, key: str) -> None:
    """Delete a credential from Keychain. Silently ignores missing entries."""
    try:
        keyring.delete_password(service, key)
    except keyring.errors.PasswordDeleteError:
        pass
    _cache.pop((service, key), None)


def cred_status(service: str, keys: list[str]) -> dict[str, str]:
    """
    Return the status of each credential key.
    Returns a dict mapping key -> 'keychain' (set) or 'missing' (not set).
    """
    return {
        k: ("keychain" if cred_get(service, k) else "missing")
        for k in keys
    }


def cred_cache_clear() -> None:
    """Clear the in-process credential cache. Mainly used in tests."""
    _cache.clear()
