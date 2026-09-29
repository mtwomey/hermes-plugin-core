"""
hermes_plugin_core.keychain — Credential storage via the macOS `security` CLI.

Policy (macOS):
  - Every read and write shells out to /usr/bin/security. The Python `keyring`
    library is NOT used, because on macOS it causes repeated password prompts:
    `set_password` deletes and recreates the item (so any prior "Always Allow"
    belonged to an object that no longer exists), and `get_password`'s
    in-process read can prompt even for a caller the item's ACL already trusts.
  - An item is created exactly once with an explicit trusted-app list (`-T`).
    Creation is a real macOS consent gate, so expect one prompt per
    (service, key) and no more.
  - Later writes update the value in place with `-U` and NO `-T`. Passing `-T`
    to an item that already exists is treated as an ACL change — a separate
    consent gate that fires on every write, even when the list is identical.
  - Never delete-and-recreate to update. That is the same bug as `keyring`,
    done by hand, and it also discards the pinned ACL.
  - Reads are cached in memory per process.

Values are passed as hex via `-X` rather than `-w <value>`, which keeps the
plaintext out of the process argument list where `ps` could read it. Note that
`-w` with NO argument prompts interactively and ignores stdin, silently storing
an empty password — never invoke it that way.

Non-macOS platforms fall back to `keyring`, which is well-behaved there.

Usage:
    from hermes_plugin_core.keychain import cred_get, cred_set, cred_delete, cred_status

    token = cred_get("hermes-jira", "api_token")     # cached per process
    cred_set("hermes-jira", "api_token", "my-token")
    cred_delete("hermes-jira", "api_token")
    cred_status("hermes-jira", ["jira_url", "api_token"])
    # -> {"jira_url": "keychain", "api_token": "missing"}
"""

from __future__ import annotations

import subprocess
import sys

SECURITY = "/usr/bin/security"

_IS_MACOS = sys.platform == "darwin"

# Module-level cache — avoids repeated Keychain access per process
_cache: dict[tuple[str, str], str | None] = {}


class KeychainWriteError(RuntimeError):
    """A credential could not be written to Keychain."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def cred_get(service: str, key: str) -> str | None:
    """Read a credential from Keychain. Returns None if not set. Cached per process."""
    cache_key = (service, key)
    if cache_key not in _cache:
        _cache[cache_key] = _read(service, key) or None
    return _cache[cache_key]


def cred_set(service: str, key: str, value: str) -> None:
    """Store a credential in Keychain and update the in-process cache.

    Creates the item with a pinned ACL on first write (one prompt, expected),
    then updates the value in place on every later write (no prompt).
    """
    if not _IS_MACOS:
        import keyring

        keyring.set_password(service, key, value)
        _cache[(service, key)] = value
        return

    if _exists(service, key):
        # Value-only update. Adding -T here would re-prompt on every write.
        cmd = [SECURITY, "add-generic-password", "-U",
               "-s", service, "-a", key, "-X", _hex(value)]
    else:
        # First creation: pin the trusted-app list once. May prompt ONCE.
        cmd = [SECURITY, "add-generic-password",
               "-s", service, "-a", key, "-X", _hex(value),
               "-T", SECURITY,        # used for all later reads and writes
               "-T", sys.executable]  # the interpreter that owns this tool

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise KeychainWriteError(
            f"security failed writing {service}/{key}: "
            f"{result.stderr.strip() or 'unknown error'}"
        )

    # Confirm the value landed — a zero exit code is not proof, and a silent
    # no-op (see the -w caveat above) would be worse than an error.
    if _read(service, key) != value:
        raise KeychainWriteError(
            f"wrote {service}/{key} but read back a different value"
        )

    _cache[(service, key)] = value


def cred_delete(service: str, key: str) -> None:
    """Delete a credential from Keychain. Silently ignores missing entries."""
    if _IS_MACOS:
        subprocess.run(
            [SECURITY, "delete-generic-password", "-s", service, "-a", key],
            capture_output=True,
        )
    else:
        import keyring
        import keyring.errors

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


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _hex(value: str) -> str:
    """Hex-encode for `-X`, keeping the plaintext out of argv."""
    return value.encode("utf-8").hex()


def _exists(service: str, key: str) -> bool:
    """True if the item is already in the Keychain.

    Decides between the create path (pins the ACL) and the update path.
    """
    return subprocess.run(
        [SECURITY, "find-generic-password", "-s", service, "-a", key],
        capture_output=True,
    ).returncode == 0


def _read(service: str, key: str) -> str | None:
    """Read the raw value, or None when absent."""
    if not _IS_MACOS:
        import keyring

        return keyring.get_password(service, key)

    try:
        result = subprocess.run(
            [SECURITY, "find-generic-password", "-s", service, "-a", key, "-w"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return None

    if result.returncode != 0:
        return None

    # `-w` prints the value plus a trailing newline. Strip only that, so a
    # secret with meaningful internal whitespace survives intact.
    return result.stdout.removesuffix("\n")
