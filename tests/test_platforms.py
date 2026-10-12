from server.platforms import capabilities_of, install_command, os_of
from tests.test_sandboxes import add_server


def test_hosts_run_their_own_os_and_linux_with_docker():
    assert capabilities_of("linux", docker=True) == "linux"
    assert capabilities_of("macos", docker=False) == "macos"
    assert capabilities_of("macos", docker=True) == "macos,linux"
    assert capabilities_of("windows", docker=True) == "windows,linux"
    assert [os_of(k) for k in ("desktop", "code", "browser", "macos", "windows")] == [
        "linux",
        "linux",
        "linux",
        "macos",
        "windows",
    ]


def test_platforms_show_what_this_install_can_run(client, alice, monkeypatch):
    monkeypatch.setattr("server.macos.local_zoovm", lambda: False)
    platforms = {p["id"]: p for p in client.get("/platforms", headers=alice).json()}
    assert platforms["linux"]["available"]
    assert not platforms["macos"]["available"]
    assert platforms["macos"]["host"] == "Mac"

    add_server("alice@example.com", "windows", capabilities="windows,linux")
    platforms = {p["id"]: p for p in client.get("/platforms", headers=alice).json()}
    assert platforms["windows"]["available"]
    assert platforms["linux"]["servers"] == 1


def test_unsupported_kind_says_which_host_to_add(client, alice, monkeypatch):
    monkeypatch.setattr("server.macos.local_zoovm", lambda: False)
    res = client.post("/sandboxes", json={"name": "mac", "kind": "macos"}, headers=alice)
    assert res.status_code == 400
    assert "add a Mac" in res.json()["error"]["message"]


def test_linux_sandboxes_can_run_on_a_mac_with_docker(client, alice):
    mac = add_server("alice@example.com", "macos", capabilities="macos,linux")
    res = client.post("/sandboxes", json={"name": "l", "kind": "code", "server_id": mac}, headers=alice)
    assert res.status_code == 201, res.text
    assert res.json()["server_id"] == mac

    windows = add_server("alice@example.com", "windows", name="win")
    res = client.post("/sandboxes", json={"name": "w", "kind": "code", "server_id": windows}, headers=alice)
    assert res.status_code == 404


def test_install_commands_carry_the_key_and_control_plane():
    unix = install_command("macos", "ssh-ed25519 AAAA zoo", "https://zoo.example/api")
    assert "install.sh | sudo bash -s -- --node" in unix
    assert "'ssh-ed25519 AAAA zoo'" in unix and "https://zoo.example/api" in unix
    assert "install-node.ps1" in install_command("windows", "ssh-ed25519 AAAA zoo", "https://zoo.example/api")


def test_join_line_host_key_is_trusted(tmp_path, monkeypatch):
    from server import ssh

    monkeypatch.setattr(ssh, "KNOWN_HOSTS", str(tmp_path / "known_hosts"))
    ssh.trust("10.0.0.5", 22, "ssh-ed25519 AAAAC3Nza root@mac")
    ssh.trust("10.0.0.5", 22, "ssh-ed25519 AAAAC3Nza root@mac")
    ssh.trust("box", 2222, "ssh-ed25519 AAAAB")
    assert (tmp_path / "known_hosts").read_text().splitlines() == [
        "10.0.0.5 ssh-ed25519 AAAAC3Nza",
        "[box]:2222 ssh-ed25519 AAAAB",
    ]
    try:
        ssh.trust("box", 22, "rm -rf /")
        raise AssertionError("accepted a bad key")
    except ValueError:
        pass
