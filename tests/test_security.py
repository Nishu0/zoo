import os

import pytest
from cryptography.fernet import InvalidToken

from db.connection import db_manager
from server import security
from server.security import REDACTED, decrypt, encrypt, redact
from tests.conftest import present


def test_redact_masks_secrets_anywhere_in_a_result():
    result = {"stdout": "token=abcdef123", "lines": ["abcdef123", ("x", "abcdef123")], "code": 0, "none": None}
    assert redact(result, ["abcdef123"]) == {
        "stdout": f"token={REDACTED}",
        "lines": [REDACTED, ("x", REDACTED)],
        "code": 0,
        "none": None,
    }


def test_redact_prefers_longest_match_and_skips_short_values():
    assert redact("abcdef-long-secret", ["abcdef", "abcdef-long-secret"]) == REDACTED
    assert redact("id=1 abc", ["abc", "1"]) == "id=1 abc"
    assert redact("nothing", []) == "nothing"


def test_encrypt_round_trip_and_tamper_detection():
    with db_manager.session() as db:
        token = encrypt("hunter2-secret", db, "workspace-a")
    assert token.startswith("zk1:") and "hunter2" not in token
    assert decrypt(token) == "hunter2-secret"
    with pytest.raises(InvalidToken):
        decrypt(token[:-4] + "AAAA")


def test_each_workspace_has_its_own_wrapped_data_key():
    with db_manager.session() as db:
        a, b = encrypt("x", db, "workspace-a"), encrypt("x", db, "workspace-b")
        key_a, key_b = a.split(":")[1], b.split(":")[1]
        assert key_a != key_b
        assert present(db.get_secret_key(id=key_a)).wrapped.startswith("local:")
    security._data_keys.clear()  # as after a restart: the key is unwrapped again from the database
    assert decrypt(a) == decrypt(b) == "x"


def test_secrets_key_rotation(client, alice, sandbox, monkeypatch):
    monkeypatch.setenv("ZOO_SECRETS_KEY_PREVIOUS", os.environ["ZOO_SECRETS_KEY"])
    monkeypatch.setenv("ZOO_SECRETS_KEY", "old-key")
    client.post("/vault/secrets", json={"name": "VAULT", "value": "vault-value"}, headers=alice)
    client.put(f"/sandboxes/{sandbox['id']}/secrets", json={"name": "LOCAL", "value": "local-value"}, headers=alice)
    with db_manager.session() as db:
        security.rotate(db)  # the workspace's key predates old-key; wrap it with old-key only
    monkeypatch.delenv("ZOO_SECRETS_KEY_PREVIOUS")

    monkeypatch.setenv("ZOO_SECRETS_KEY", "new-key")
    security._data_keys.clear()
    with db_manager.session() as db, pytest.raises(InvalidToken):
        decrypt(next(iter(db.list_all_vault_secrets())).ciphertext)

    monkeypatch.setenv("ZOO_SECRETS_KEY_PREVIOUS", "old-key")
    with db_manager.session() as db:
        counts = security.rotate(db)
    # the values stay as they are; only the data keys around them are wrapped again
    assert counts["data_keys"] >= 1

    monkeypatch.delenv("ZOO_SECRETS_KEY_PREVIOUS")
    security._data_keys.clear()
    with db_manager.session() as db:
        assert decrypt(next(iter(db.list_all_vault_secrets())).ciphertext) == "vault-value"
        assert decrypt(next(iter(db.list_all_sandbox_secrets())).secret_ref) == "local-value"
