"""A signed read-only probe distinguishes stale enrollment from service downtime."""

import base64
import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from apps.server import nodes
from db.connection import db_manager
from tests.conftest import V1Client, sql
from tests.test_nodes import join, new_token


@pytest.fixture(autouse=True)
def isolated_authority(monkeypatch):
    monkeypatch.setattr(nodes, "_authority", None)


@pytest.fixture
def enrollment(client, alice):
    token = new_token(client, alice)
    key, response = join(client, token["token"])
    assert response.status_code == 200, response.text
    return SimpleNamespace(key=key, token=token["token"], **response.json())


def proof(enrollment, *, timestamp=None, nonce="a" * 64):
    timestamp = int(nodes.now().timestamp()) if timestamp is None else timestamp
    message = f"zoo-node-enrollment-v1\n{timestamp}\n{nonce}\n".encode()
    signature = enrollment.key.sign(message, ec.ECDSA(hashes.SHA256()))
    return {
        "certificate": enrollment.certificate,
        "timestamp": timestamp,
        "nonce": nonce,
        "signature": base64.b64encode(signature).decode(),
    }


def issued_certificate(key, name, *, expired=False, server=False, ca=False):
    authority = nodes.authority()
    now = nodes.now()
    return nodes.pem(
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(authority.certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=2))
        .not_valid_after(now + datetime.timedelta(days=-1 if expired else 2))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .sign(authority.key, hashes.SHA256())
    )


def test_valid_same_authority_registration_is_reusable_without_another_token(client, alice, enrollment):
    payload = proof(enrollment)
    expected = {"status": "current", "node_id": enrollment.node_id, "server_id": enrollment.server_id}
    for _ in range(2):
        response = client.post("/v1/nodes/enrollment/status", json=payload)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == expected
    assert nodes.enrollment_status(**payload) == expected
    assert len(client.get("/v1/servers", headers=alice).json()) == 1
    assert join(client, enrollment.token)[1].status_code == 401  # Original token is consumed; reuse needs no new one.


def test_read_only_status_probe_does_not_spend_a_fresh_repair_token(client, alice, enrollment):
    repair = new_token(client, alice, name="repair")
    assert client.post("/v1/nodes/enrollment/status", json=proof(enrollment)).json()["status"] == "current"
    assert join(client, repair["token"])[1].status_code == 200


def test_same_url_with_reset_control_plane_authority_requires_reenrollment(client, enrollment, monkeypatch):
    payload = proof(enrollment)
    sql("DELETE FROM node_authority")
    monkeypatch.setattr(nodes, "_authority", None)
    response = client.post("/v1/nodes/enrollment/status", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "reenroll", "reason": "authority_changed"}


@pytest.mark.parametrize("deleted", ["node", "server"])
def test_deleted_registration_requires_reenrollment_even_with_the_same_authority(client, enrollment, deleted):
    payload = proof(enrollment)
    if deleted == "node":
        sql("DELETE FROM nodes WHERE id = ?", enrollment.node_id)
    else:
        sql("DELETE FROM servers WHERE id = ?", enrollment.server_id)
    response = client.post("/v1/nodes/enrollment/status", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "reenroll", "reason": "registration_missing"}


@pytest.mark.parametrize("changed", ["serial", "expiry"])
def test_replaced_credentials_are_not_mistaken_for_a_current_registration(client, enrollment, changed):
    if changed == "serial":
        sql("UPDATE nodes SET serial = 'replacement' WHERE id = ?", enrollment.node_id)
    else:
        sql("UPDATE nodes SET cert_expires_at = cert_expires_at + interval '1 minute' WHERE id = ?", enrollment.node_id)
    response = client.post("/v1/nodes/enrollment/status", json=proof(enrollment))
    assert response.status_code == 200
    assert response.json() == {"status": "reenroll", "reason": "registration_replaced"}


def test_expired_credentials_are_classified_only_after_valid_possession_proof(client, enrollment):
    enrollment.certificate = issued_certificate(enrollment.key, enrollment.node_id, expired=True)
    response = client.post("/v1/nodes/enrollment/status", json=proof(enrollment))
    assert response.status_code == 200
    assert response.json() == {"status": "reenroll", "reason": "expired"}


@pytest.mark.parametrize("tamper", ["signature", "nonce", "timestamp", "key", "certificate"])
def test_invalid_proofs_never_authorize_reenrollment_or_disclose_current_ids(client, enrollment, tamper):
    payload = proof(enrollment)
    if tamper == "signature":
        payload["signature"] = "***"
    elif tamper == "nonce":
        payload["nonce"] = "b" * 64
    elif tamper == "timestamp":
        payload["timestamp"] += 1
    elif tamper == "key":
        other = ec.generate_private_key(ec.SECP256R1())
        message = f"zoo-node-enrollment-v1\n{payload['timestamp']}\n{payload['nonce']}\n".encode()
        payload["signature"] = base64.b64encode(other.sign(message, ec.ECDSA(hashes.SHA256()))).decode()
    else:
        payload["certificate"] = "not a PEM certificate"
    response = client.post("/v1/nodes/enrollment/status", json=payload)
    assert response.status_code == 401
    assert "node_id" not in response.json() and "server_id" not in response.json()
    assert "status" not in response.json()


@pytest.mark.parametrize("offset", [-121, 121])
def test_clock_stale_or_future_proofs_fail_closed(client, enrollment, offset):
    payload = proof(enrollment, timestamp=int(nodes.now().timestamp()) + offset)
    response = client.post("/v1/nodes/enrollment/status", json=payload)
    assert response.status_code == 401
    assert "status" not in response.json()


@pytest.mark.parametrize(
    "field,value", [("nonce", "A" * 64), ("nonce", "a" * 63), ("timestamp", "123"), ("certificate", "")]
)
def test_malformed_probe_fields_are_not_a_stale_registration_signal(client, enrollment, field, value):
    payload = {**proof(enrollment), field: value}
    response = client.post("/v1/nodes/enrollment/status", json=payload)
    assert response.status_code == 422
    assert "status" not in response.json()


@pytest.mark.parametrize("invalid", ["server_certificate", "ca_certificate", "noncanonical_identity"])
def test_only_canonical_node_client_certificates_can_probe_status(client, enrollment, invalid):
    name = enrollment.node_id if invalid != "noncanonical_identity" else "ABCDEFAB-CDEF-ABCD-EFAB-CDEFABCDEFAB"
    enrollment.certificate = issued_certificate(
        enrollment.key,
        name,
        server=invalid == "server_certificate",
        ca=invalid == "ca_certificate",
    )
    response = client.post("/v1/nodes/enrollment/status", json=proof(enrollment))
    assert response.status_code == 401


def test_storage_failure_propagates_instead_of_authorizing_reenrollment(client, enrollment, monkeypatch):
    payload = proof(enrollment)
    monkeypatch.setattr(db_manager, "session", Mock(side_effect=ConnectionError("node inventory unavailable")))
    with pytest.raises(ConnectionError, match="node inventory unavailable"):
        nodes.enrollment_status(**payload)
    http = V1Client(client.app, raise_server_exceptions=False)
    response = http.post("/v1/nodes/enrollment/status", json=payload)
    assert response.status_code == 500
    assert response.json().get("status") not in ("current", "reenroll")
