"""Custom domains: Caddy's on-demand TLS check and the DNS verification shown on each domain."""

import socket

import pytest

from apps.server import admin_api


@pytest.fixture
def dns(monkeypatch):
    """Answers lookups from a table instead of the network."""
    records: dict[str, list[str]] = {}

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host not in records:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
            for ip in records[host]
        ]

    monkeypatch.setattr(admin_api.socket, "getaddrinfo", getaddrinfo)
    return records


def add(client, admin, hostname):
    res = client.post("/admin/domains", json={"hostname": hostname}, headers=admin)
    assert res.status_code == 201, res.text
    return res.json()


def test_caddy_may_only_issue_certificates_for_known_domains(client, admin, dns, monkeypatch):
    add(client, admin, "zoo.example.com")
    monkeypatch.setenv("ZOO_DOMAIN", "Main.Example.com")
    for name in ("zoo.example.com", "ZOO.Example.COM", "zoo.example.com.", "main.example.com"):
        assert client.get("/domains/check", params={"domain": name}).status_code == 200, name
    for name in ("evil.example.com", "sub.zoo.example.com", "example.com", "", " "):
        assert client.get("/domains/check", params={"domain": name}).status_code == 404, name
    assert client.get("/domains/check").status_code == 404


def test_the_check_needs_no_login_but_unset_main_domain_matches_nothing(client, dns, monkeypatch):
    monkeypatch.delenv("ZOO_DOMAIN", raising=False)
    assert client.get("/domains/check", params={"domain": ""}).status_code == 404
    assert client.get("/domains/check", params={"domain": "anything.example.com"}).status_code == 404


def test_removed_domains_stop_getting_certificates(client, admin, dns):
    domain = add(client, admin, "zoo.example.com")
    client.delete(f"/admin/domains/{domain['id']}", headers=admin)
    assert client.get("/domains/check", params={"domain": "zoo.example.com"}).status_code == 404


def test_hostnames_are_stored_lower_case(client, admin, dns):
    assert add(client, admin, "Zoo.Example.com.")["hostname"] == "zoo.example.com"
    assert client.post("/admin/domains", json={"hostname": "ZOO.example.com"}, headers=admin).status_code == 409


def test_dns_verification(client, admin, dns, monkeypatch):
    monkeypatch.setattr(admin_api, "PUBLIC_IP", "203.0.113.7")
    dns["zoo.example.com"] = ["203.0.113.7"]
    dns["elsewhere.example.com"] = ["198.51.100.1"]
    here = add(client, admin, "zoo.example.com")
    assert here["addresses"] == ["203.0.113.7"] and here["points_here"] is True
    there = add(client, admin, "elsewhere.example.com")
    assert there["points_here"] is False
    missing = add(client, admin, "nxdomain.example.com")
    assert missing["addresses"] == [] and missing["points_here"] is False

    # after fixing the record, verify again
    dns["elsewhere.example.com"] = ["203.0.113.7", "198.51.100.1"]
    res = client.post(f"/admin/domains/{there['id']}/verify", headers=admin)
    assert res.status_code == 200 and res.json()["points_here"] is True
    assert client.post("/admin/domains/nope/verify", headers=admin).status_code == 404
    listed = {d["hostname"]: d["points_here"] for d in client.get("/admin/domains", headers=admin).json()}
    assert listed == {"zoo.example.com": True, "elsewhere.example.com": True, "nxdomain.example.com": False}


def test_dns_verification_with_ipv6_and_several_addresses(client, admin, dns, monkeypatch):
    monkeypatch.setattr(admin_api, "PUBLIC_IP", "203.0.113.7, 2001:db8::7")
    dns["v6.example.com"] = ["2001:db8::7"]
    assert add(client, admin, "v6.example.com")["points_here"] is True


def test_without_a_public_ip_verification_is_unknown(client, admin, dns, monkeypatch):
    monkeypatch.setattr(admin_api, "PUBLIC_IP", None)
    dns["zoo.example.com"] = ["203.0.113.7"]
    assert add(client, admin, "zoo.example.com")["points_here"] is None


def test_verification_is_admin_only(client, admin, alice, dns):
    domain = add(client, admin, "zoo.example.com")
    assert client.post(f"/admin/domains/{domain['id']}/verify", headers=alice).status_code == 403
