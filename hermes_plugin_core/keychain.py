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

import subprocess
import sys

import keyring
import keyring.errors


class KeychainWriteError(RuntimeError):
    """A credential could not be written to Keychain, including via fallback."""


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
    """Store a credential in Keychain and update the in-process cache.

    Keychain items can end up in a state where an existing item is readable
    but neither updatable nor deletable via the Security framework, failing
    with errSecInvalidOwnerEdit (-25244, "Invalid attempt to change the owner
    of this item"). Creating a *new* item in the same service still works, so
    recover by removing the item with the `security` CLI — which can delete
    what keyring cannot — and writing it back fresh.

    This matters for rotating credentials: without it, every refreshed OAuth
    token is silently discarded and the stored one is frozen at its original
    issuance, so it eventually hard-expires and forces an interactive sign-in.
    """
    try:
        keyring.set_password(service, key, value)
    except Exception as exc:
        if not _is_owner_edit_error(exc) or sys.platform != "darwin":
            raise
        _recreate_via_security_cli(service, key, value)

    _cache[(service, key)] = value


def _is_owner_edit_error(exc: Exception) -> bool:
    """True for the -25244 / errSecInvalidOwnerEdit failure described above."""
    return "-25244" in str(exc) or "25244" in str(exc)


def _recreate_via_security_cli(service: str, key: str, value: str) -> None:
    """Delete then re-add a stuck Keychain item using the `security` CLI.

    The value is passed with ``-X`` as a hex string rather than ``-w <value>``.
    ``-w`` with no argument prompts interactively and ignores stdin, which
    silently stores an empty password; ``-w <value>`` puts the plaintext in
    argv where `ps` can read it. Hex is still argv-visible and trivially
    reversible, but it keeps the literal secret out of the process list.
    """
    subprocess.run(
        ["security", "delete-generic-password", "-s", service, "-a", key],
        capture_output=True,
    )

    # -U updates in place if the delete above was itself refused.
    result = subprocess.run(
        [
            "security", "add-generic-password",
            "-s", service, "-a", key, "-U",
            "-X", value.encode("utf-8").hex(),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise KeychainWriteError(
            f"could not write {service}/{key} to Keychain: keyring reported "
            f"errSecInvalidOwnerEdit (-25244) and the security CLI fallback "
            f"failed: {result.stderr.strip() or 'unknown error'}"
        )

    # Confirm the value landed; a silent no-op would be worse than an error.
    if keyring.get_password(service, key) != value:
        raise KeychainWriteError(
            f"wrote {service}/{key} via the security CLI but read back a "
            f"different value"
        )


def cred_delete(service: str, key: str) -> None:
    """Delete a credential from Keychain. Silently ignores missing entries.

    Falls back to the `security` CLI for items stuck in the
    errSecInvalidOwnerEdit state, which keyring can neither update nor delete.
    """
    try:
        keyring.delete_password(service, key)
    except keyring.errors.PasswordDeleteError as exc:
        # A genuinely absent entry is fine; a stuck item is not.
        if _is_owner_edit_error(exc) and sys.platform == "darwin":
            subprocess.run(
                ["security", "delete-generic-password", "-s", service, "-a", key],
                capture_output=True,
            )
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
