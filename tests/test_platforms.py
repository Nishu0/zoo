from server.platforms import capabilities_of, os_of
from tests.test_sandboxes import add_server


def test_hosts_run_their_native_os_and_linux_containers():
    assert capabilities_of("linux") == "linux"
    assert capabilities_of("macos") == "macos,linux"
    assert capabilities_of("windows") == "windows,linux"
    assert [os_of(k) for k in ("desktop", "code", "browser", "macos", "windows")] == [
        "linux",
        "linux",
        "linux",
        "macos",
        "windows",
    ]


def test_platforms_show_what_this_install_can_run(client, alice):
    platforms = {p["id"]: p for p in client.get("/platforms", headers=alice).json()}
    assert platforms["linux"]["available"]
    assert not platforms["macos"]["available"]
    assert platforms["macos"]["host"] == "Mac"

    add_server("alice@example.com", "windows")
    platforms = {p["id"]: p for p in client.get("/platforms", headers=alice).json()}
    assert platforms["windows"]["available"]
    assert platforms["linux"]["servers"] == 0


def test_unsupported_kind_says_which_host_to_add(client, alice):
    res = client.post("/sandboxes", json={"name": "mac", "kind": "macos"}, headers=alice)
    assert res.status_code == 400
    assert "add a Mac" in res.json()["error"]["message"]


def test_linux_sandboxes_require_a_ready_docker_driver(client, alice):
    mac = add_server("alice@example.com", "macos")
    windows = add_server("alice@example.com", "windows", name="win")
    for server_id in (mac, windows):
        res = client.post("/v1/sandboxes", json={"kind": "code", "server_id": server_id}, headers=alice)
        assert res.status_code == 503 and res.json()["error"]["code"] == "unavailable"
