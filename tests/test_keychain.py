"""Tests for Keychain credential storage.

Focus: the errSecInvalidOwnerEdit (-25244) recovery path. A Keychain item can
become readable-but-not-writable; keyring then raises on every update and, left
unhandled, a rotating OAuth token is silently discarded until it hard-expires.
These assert the recovery behaves, and that unrelated failures still propagate.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_plugin_core import keychain as kc


OWNER_EDIT_MSG = "Can't store password on keychain: (-25244, 'Unknown Error')"


@pytest.fixture(autouse=True)
def clear_cache():
    kc.cred_cache_clear()
    yield
    kc.cred_cache_clear()


@pytest.fixture
def darwin(monkeypatch):
    monkeypatch.setattr(kc.sys, "platform", "darwin")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_cred_set_uses_keyring_when_it_works(monkeypatch):
    calls = []
    monkeypatch.setattr(kc.keyring, "set_password", lambda s, k, v: calls.append((s, k, v)))
    # Any CLI use would be wrong here.
    monkeypatch.setattr(kc.subprocess, "run", lambda *a, **kw: pytest.fail("CLI must not run"))

    kc.cred_set("svc", "key", "val")

    assert calls == [("svc", "key", "val")]
    assert kc.cred_get("svc", "key") == "val"  # cache updated


# ---------------------------------------------------------------------------
# -25244 recovery
# ---------------------------------------------------------------------------
def test_owner_edit_error_triggers_cli_recreate(monkeypatch, darwin):
    monkeypatch.setattr(
        kc.keyring, "set_password",
        lambda s, k, v: (_ for _ in ()).throw(Exception(OWNER_EDIT_MSG)),
    )
    monkeypatch.setattr(kc.keyring, "get_password", lambda s, k: "val")

    ran = []

    def fake_run(cmd, **kwargs):
        ran.append((cmd, kwargs.get("input")))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(kc.subprocess, "run", fake_run)

    kc.cred_set("svc", "key", "val")

    assert [c[0][1] for c in ran] == ["delete-generic-password", "add-generic-password"]
    # `-w` with no argument prompts and ignores stdin (storing an empty
    # password); `-w <value>` leaks plaintext to `ps`. Hex via -X avoids both.
    add_cmd, _ = ran[1]
    assert "-X" in add_cmd
    assert "val" not in add_cmd
    assert add_cmd[add_cmd.index("-X") + 1] == "val".encode("utf-8").hex()
    assert kc.cred_get("svc", "key") == "val"


def test_recreate_verifies_readback(monkeypatch, darwin):
    """A CLI exit code of 0 is not proof the value landed."""
    monkeypatch.setattr(
        kc.keyring, "set_password",
        lambda s, k, v: (_ for _ in ()).throw(Exception(OWNER_EDIT_MSG)),
    )
    monkeypatch.setattr(kc.keyring, "get_password", lambda s, k: "something-else")
    monkeypatch.setattr(
        kc.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    with pytest.raises(kc.KeychainWriteError, match="read back a different value"):
        kc.cred_set("svc", "key", "val")


def test_recreate_raises_when_cli_fails(monkeypatch, darwin):
    monkeypatch.setattr(
        kc.keyring, "set_password",
        lambda s, k, v: (_ for _ in ()).throw(Exception(OWNER_EDIT_MSG)),
    )

    def fake_run(cmd, **kwargs):
        rc = 1 if "add-generic-password" in cmd else 0
        return subprocess.CompletedProcess(cmd, rc, "", "denied")

    monkeypatch.setattr(kc.subprocess, "run", fake_run)

    with pytest.raises(kc.KeychainWriteError, match="denied"):
        kc.cred_set("svc", "key", "val")


def test_failed_write_does_not_poison_cache(monkeypatch, darwin):
    """A failed write must not leave the cache claiming the new value."""
    monkeypatch.setattr(
        kc.keyring, "set_password",
        lambda s, k, v: (_ for _ in ()).throw(Exception(OWNER_EDIT_MSG)),
    )
    monkeypatch.setattr(
        kc.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "nope"),
    )

    with pytest.raises(kc.KeychainWriteError):
        kc.cred_set("svc", "key", "new")

    monkeypatch.setattr(kc.keyring, "get_password", lambda s, k: "old")
    assert kc.cred_get("svc", "key") == "old"


# ---------------------------------------------------------------------------
# Unrelated failures must not be swallowed
# ---------------------------------------------------------------------------
def test_other_errors_propagate(monkeypatch, darwin):
    monkeypatch.setattr(
        kc.keyring, "set_password",
        lambda s, k, v: (_ for _ in ()).throw(Exception("keychain is locked")),
    )
    monkeypatch.setattr(kc.subprocess, "run", lambda *a, **kw: pytest.fail("CLI must not run"))

    with pytest.raises(Exception, match="locked"):
        kc.cred_set("svc", "key", "val")


def test_no_cli_fallback_off_darwin(monkeypatch):
    monkeypatch.setattr(kc.sys, "platform", "linux")
    monkeypatch.setattr(
        kc.keyring, "set_password",
        lambda s, k, v: (_ for _ in ()).throw(Exception(OWNER_EDIT_MSG)),
    )
    monkeypatch.setattr(kc.subprocess, "run", lambda *a, **kw: pytest.fail("no `security` here"))

    with pytest.raises(Exception, match="25244"):
        kc.cred_set("svc", "key", "val")


# ---------------------------------------------------------------------------
# cred_delete
# ---------------------------------------------------------------------------
def test_delete_falls_back_to_cli_on_owner_edit(monkeypatch, darwin):
    import keyring.errors

    monkeypatch.setattr(
        kc.keyring, "delete_password",
        lambda s, k: (_ for _ in ()).throw(
            keyring.errors.PasswordDeleteError(
                "Can't delete password in keychain: (-25244, 'Unknown Error')"
            )
        ),
    )
    ran = []
    monkeypatch.setattr(
        kc.subprocess, "run",
        lambda cmd, **kw: (ran.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1],
    )

    kc.cred_delete("svc", "key")

    assert ran and "delete-generic-password" in ran[0]


def test_delete_ignores_missing_entry(monkeypatch, darwin):
    """An absent credential is not an error and needs no CLI call."""
    import keyring.errors

    monkeypatch.setattr(
        kc.keyring, "delete_password",
        lambda s, k: (_ for _ in ()).throw(keyring.errors.PasswordDeleteError("not found")),
    )
    monkeypatch.setattr(kc.subprocess, "run", lambda *a, **kw: pytest.fail("CLI must not run"))

    kc.cred_delete("svc", "key")  # must not raise
