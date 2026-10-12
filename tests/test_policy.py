from tests.conftest import runtime_of


def effects(client, headers, sid) -> dict[str, str]:
    return {
        f"{p['permission']}.{p['action']}": p["effect"]
        for p in client.get(f"/sandboxes/{sid}/permissions", headers=headers).json()
    }


def test_permissions_default_to_allow(client, alice, sandbox):
    assert set(effects(client, alice, sandbox["id"]).values()) == {"allow"}


def test_denied_permission_blocks_its_tools_only(client, alice, sandbox):
    sid = sandbox["id"]
    res = client.put(
        f"/sandboxes/{sid}/permissions", json={"permission": "shell", "action": "exec", "effect": "deny"}, headers=alice
    )
    assert res.status_code == 200
    assert effects(client, alice, sid)["shell.exec"] == "deny"

    for path, body in [
        (f"/sandboxes/{sid}/exec", {"command": "id"}),
        (f"/sandboxes/{sid}/tools/execute_command", {"command": "id"}),
        (f"/sandboxes/{sid}/tools/fetch_url", {"url": "https://example.com"}),
    ]:
        res = client.post(path, json=body, headers=alice)
        assert res.status_code == 403, path
        assert res.json()["error"]["message"] == "shell.exec is denied for this sandbox"
    assert client.post(f"/sandboxes/{sid}/tools/screenshot", headers=alice).status_code == 200

    client.put(
        f"/sandboxes/{sid}/permissions",
        json={"permission": "shell", "action": "exec", "effect": "allow"},
        headers=alice,
    )
    assert client.post(f"/sandboxes/{sid}/exec", json={"command": "id"}, headers=alice).status_code == 200


def test_each_permission_guards_its_tools(client, alice, sandbox):
    sid = sandbox["id"]
    cases = {
        ("screen", "read"): ("screenshot", {}),
        ("input", "control"): ("click", {"x": 1, "y": 1}),
        ("files", "read"): ("read_file", {"path": "a"}),
        ("files", "write"): ("write_file", {"path": "a", "content": "b"}),
    }
    for (permission, action), (tool, args) in cases.items():
        client.put(
            f"/sandboxes/{sid}/permissions",
            json={"permission": permission, "action": action, "effect": "deny"},
            headers=alice,
        )
        assert client.post(f"/sandboxes/{sid}/tools/{tool}", json=args, headers=alice).status_code == 403, tool
        client.put(
            f"/sandboxes/{sid}/permissions",
            json={"permission": permission, "action": action, "effect": "allow"},
            headers=alice,
        )
        assert client.post(f"/sandboxes/{sid}/tools/{tool}", json=args, headers=alice).status_code == 200, tool


def test_unknown_permission_is_rejected(client, alice, sandbox):
    res = client.put(
        f"/sandboxes/{sandbox['id']}/permissions",
        json={"permission": "root", "action": "all", "effect": "deny"},
        headers=alice,
    )
    assert res.status_code == 422


def test_network_policy_crud_is_enforced(client, alice, sandbox, fake):
    sid = sandbox["id"]
    container = fake.containers[runtime_of(sid)]
    assert client.get(f"/sandboxes/{sid}/network", headers=alice).json() == {
        "default_action": "allow",
        "allow_dns": True,
        "rules": [],
    }

    net = client.put(
        f"/sandboxes/{sid}/network", json={"default_action": "deny", "allow_dns": False}, headers=alice
    ).json()
    assert net["default_action"] == "deny"
    assert container.network == ("deny", False, [])

    res = client.post(
        f"/sandboxes/{sid}/network/rules",
        json={"rule_type": "domain", "value": " PyPI.org ", "effect": "allow"},
        headers=alice,
    )
    assert res.status_code == 201
    [rule] = res.json()["rules"]
    assert rule["value"] == "pypi.org"
    assert container.network == ("deny", False, [("domain", "pypi.org", "allow")])

    assert client.delete(f"/sandboxes/{sid}/network/rules/{rule['id']}", headers=alice).json()["rules"] == []
    assert container.network == ("deny", False, [])
    assert client.delete(f"/sandboxes/{sid}/network/rules/{rule['id']}", headers=alice).status_code == 404


def test_network_rule_validation(client, alice, sandbox):
    sid = sandbox["id"]
    for body in [
        {"rule_type": "port", "value": "22", "effect": "deny"},
        {"rule_type": "domain", "value": "", "effect": "deny"},
        {"rule_type": "domain", "value": "a.com", "effect": "maybe"},
    ]:
        assert client.post(f"/sandboxes/{sid}/network/rules", json=body, headers=alice).status_code == 422


def test_policy_survives_restart(client, alice, sandbox, fake):
    sid = sandbox["id"]
    client.put(f"/sandboxes/{sid}/network", json={"default_action": "deny", "allow_dns": True}, headers=alice)
    client.put(f"/sandboxes/{sid}/apps/firefox-esr", json={"effect": "deny"}, headers=alice)
    client.post(f"/sandboxes/{sid}/stop", headers=alice)
    client.post(f"/sandboxes/{sid}/start", headers=alice)
    container = fake.containers[runtime_of(sid)]
    assert container.network == ("deny", True, [])
    assert container.apps == {"firefox-esr": "deny"}


def test_app_permissions(client, alice, sandbox, fake):
    sid = sandbox["id"]
    apps = client.get(f"/sandboxes/{sid}/apps", headers=alice).json()
    assert apps == [
        {"name": "Firefox", "binary": "firefox-esr", "effect": "allow"},
        {"name": "Terminal", "binary": "xfce4-terminal", "effect": "allow"},
    ]
    res = client.put(f"/sandboxes/{sid}/apps/firefox-esr", json={"effect": "deny"}, headers=alice)
    assert {a["binary"]: a["effect"] for a in res.json()}["firefox-esr"] == "deny"
    assert fake.containers[runtime_of(sid)].apps == {"firefox-esr": "deny"}
    assert client.put(f"/sandboxes/{sid}/apps/not-installed", json={"effect": "deny"}, headers=alice).status_code == 404


def test_sandbox_secrets_crud(client, alice, sandbox):
    sid = sandbox["id"]
    [secret] = client.put(f"/sandboxes/{sid}/secrets", json={"name": "TOKEN", "value": "one"}, headers=alice).json()
    assert secret["name"] == "TOKEN" and secret["enabled"]
    [replaced] = client.put(f"/sandboxes/{sid}/secrets", json={"name": "TOKEN", "value": "two"}, headers=alice).json()
    assert replaced["id"] != secret["id"]
    assert (
        client.put(f"/sandboxes/{sid}/secrets", json={"name": "1BAD", "value": "x"}, headers=alice).status_code == 422
    )
    assert client.delete(f"/sandboxes/{sid}/secrets/{replaced['id']}", headers=alice).json() == []
    assert client.delete(f"/sandboxes/{sid}/secrets/{replaced['id']}", headers=alice).status_code == 404


def test_policy_is_private(client, bob, sandbox):
    sid = sandbox["id"]
    for method, path, body in [
        ("get", f"/sandboxes/{sid}/permissions", None),
        ("put", f"/sandboxes/{sid}/permissions", {"permission": "shell", "action": "exec", "effect": "allow"}),
        ("get", f"/sandboxes/{sid}/network", None),
        ("put", f"/sandboxes/{sid}/network", {"default_action": "allow", "allow_dns": True}),
        ("get", f"/sandboxes/{sid}/secrets", None),
        ("put", f"/sandboxes/{sid}/secrets", {"name": "X", "value": "y"}),
        ("get", f"/sandboxes/{sid}/apps", None),
    ]:
        assert client.request(method, path, json=body, headers=bob).status_code == 404, path
