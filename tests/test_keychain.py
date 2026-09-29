"""Tests for Keychain credential storage.

Focus: the prompt-avoidance contract on macOS. Each rule below was established
by live testing against real Keychain items — getting any of them wrong brings
back the password dialog on every credential write:

  - create once WITH -T (pins the ACL)
  - update in place with -U and NEVER -T (a -T on an existing item is an ACL
    change, which is its own consent gate)
  - never delete-and-recreate to update (discards the pinned ACL)
  - never use the `keyring` library on macOS, for reads or writes
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


@pytest.fixture(autouse=True)
def clear_cache():
    kc.cred_cache_clear()
    yield
    kc.cred_cache_clear()


@pytest.fixture
def macos(monkeypatch):
    monkeypatch.setattr(kc, "_IS_MACOS", True)


class FakeSecurity:
    """Records `security` invocations and simulates item presence."""

    def __init__(self, existing: dict[tuple[str, str], str] | None = None):
        self.items = dict(existing or {})
        self.calls: list[list[str]] = []

    def run(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        verb = cmd[1]
        svc = cmd[cmd.index("-s") + 1]
        acct = cmd[cmd.index("-a") + 1]

        if verb == "find-generic-password":
            if (svc, acct) not in self.items:
                return subprocess.CompletedProcess(cmd, 44, "", "")
            out = self.items[(svc, acct)] + "\n" if "-w" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, out, "")

        if verb == "add-generic-password":
            self.items[(svc, acct)] = bytes.fromhex(
                cmd[cmd.index("-X") + 1]
            ).decode()
            return subprocess.CompletedProcess(cmd, 0, "", "")

        if verb == "delete-generic-password":
            self.items.pop((svc, acct), None)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        raise AssertionError(f"unexpected verb {verb}")

    def verbs(self):
        return [c[1] for c in self.calls]

    def adds(self):
        return [c for c in self.calls if c[1] == "add-generic-password"]


@pytest.fixture
def sec(monkeypatch, macos):
    fake = FakeSecurity()
    monkeypatch.setattr(kc.subprocess, "run", fake.run)
    return fake


# ---------------------------------------------------------------------------
# The prompt-avoidance contract
# ---------------------------------------------------------------------------
def test_first_write_pins_acl_with_dash_T(sec):
    """Creation is the one place -T belongs: it pins trust so nothing re-prompts."""
    kc.cred_set("svc", "key", "v1")

    add = sec.adds()[0]
    assert "-T" in add
    assert kc.SECURITY in add                      # all later access goes through it
    assert sys.executable in add                   # the owning interpreter
    assert "-U" not in add                         # creating, not updating


def test_later_write_updates_in_place_without_dash_T(sec):
    """-T on an existing item is an ACL change and prompts on EVERY write."""
    kc.cred_set("svc", "key", "v1")
    sec.calls.clear()

    kc.cred_set("svc", "key", "v2")

    add = sec.adds()[0]
    assert "-U" in add
    assert "-T" not in add, "passing -T to an existing item re-prompts every write"
    assert sec.items[("svc", "key")] == "v2"


def test_update_never_deletes_the_item(sec):
    """delete+recreate is the keyring bug by hand — it discards the pinned ACL."""
    kc.cred_set("svc", "key", "v1")
    sec.calls.clear()

    kc.cred_set("svc", "key", "v2")

    assert "delete-generic-password" not in sec.verbs()


def test_secret_is_never_in_argv(sec):
    """-X hex keeps plaintext out of the process list; bare -w would prompt."""
    kc.cred_set("svc", "key", "super-secret")

    for call in sec.calls:
        assert "super-secret" not in call
    add = sec.adds()[0]
    assert add[add.index("-X") + 1] == "super-secret".encode().hex()


def test_keyring_is_not_used_on_macos(monkeypatch, sec):
    """keyring prompts on macOS for both reads and writes — it must not be touched."""
    import keyring

    monkeypatch.setattr(
        keyring, "set_password",
        lambda *a: pytest.fail("keyring.set_password must not run on macOS"),
    )
    monkeypatch.setattr(
        keyring, "get_password",
        lambda *a: pytest.fail("keyring.get_password must not run on macOS"),
    )

    kc.cred_set("svc", "key", "v1")
    kc.cred_cache_clear()
    assert kc.cred_get("svc", "key") == "v1"


# ---------------------------------------------------------------------------
# Read / roundtrip behavior
# ---------------------------------------------------------------------------
def test_missing_credential_returns_none(sec):
    assert kc.cred_get("svc", "absent") is None


def test_read_strips_only_the_trailing_newline(monkeypatch, macos):
    """`-w` appends a newline; a secret's own whitespace must survive."""
    value = "  padded secret  "

    def fake_run(cmd, **kwargs):
        if cmd[1] == "find-generic-password" and "-w" in cmd:
            return subprocess.CompletedProcess(cmd, 0, value + "\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(kc.subprocess, "run", fake_run)
    assert kc.cred_get("svc", "key") == value


def test_reads_are_cached(sec):
    kc.cred_set("svc", "key", "v1")
    kc.cred_cache_clear()

    kc.cred_get("svc", "key")
    before = len(sec.calls)
    kc.cred_get("svc", "key")

    assert len(sec.calls) == before, "second read should hit the cache"


def test_cred_status_reports_presence(sec):
    kc.cred_set("svc", "present", "v")
    assert kc.cred_status("svc", ["present", "absent"]) == {
        "present": "keychain",
        "absent": "missing",
    }


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------
def test_write_failure_raises(monkeypatch, macos):
    def fake_run(cmd, **kwargs):
        if cmd[1] == "add-generic-password":
            return subprocess.CompletedProcess(cmd, 1, "", "denied")
        return subprocess.CompletedProcess(cmd, 44, "", "")

    monkeypatch.setattr(kc.subprocess, "run", fake_run)

    with pytest.raises(kc.KeychainWriteError, match="denied"):
        kc.cred_set("svc", "key", "v")


def test_write_verifies_readback(monkeypatch, macos):
    """A zero exit code is not proof the value landed."""
    def fake_run(cmd, **kwargs):
        if cmd[1] == "find-generic-password":
            if "-w" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "something-else\n", "")
            return subprocess.CompletedProcess(cmd, 44, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(kc.subprocess, "run", fake_run)

    with pytest.raises(kc.KeychainWriteError, match="read back a different value"):
        kc.cred_set("svc", "key", "v")


def test_failed_write_does_not_poison_cache(monkeypatch, macos):
    def fake_run(cmd, **kwargs):
        if cmd[1] == "add-generic-password":
            return subprocess.CompletedProcess(cmd, 1, "", "nope")
        if "-w" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "old\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(kc.subprocess, "run", fake_run)

    with pytest.raises(kc.KeychainWriteError):
        kc.cred_set("svc", "key", "new")

    assert kc.cred_get("svc", "key") == "old"


def test_delete_removes_item_and_cache(sec):
    kc.cred_set("svc", "key", "v")
    kc.cred_delete("svc", "key")

    assert ("svc", "key") not in sec.items
    assert kc.cred_get("svc", "key") is None


# ---------------------------------------------------------------------------
# Non-macOS
# ---------------------------------------------------------------------------
def test_non_macos_uses_keyring(monkeypatch):
    monkeypatch.setattr(kc, "_IS_MACOS", False)
    import keyring

    store = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, k, v: store.__setitem__((s, k), v))
    monkeypatch.setattr(keyring, "get_password", lambda s, k: store.get((s, k)))
    monkeypatch.setattr(
        kc.subprocess, "run",
        lambda *a, **kw: pytest.fail("`security` does not exist off macOS"),
    )

    kc.cred_set("svc", "key", "v1")
    kc.cred_cache_clear()
    assert kc.cred_get("svc", "key") == "v1"
