from apps.server.sandbox_api import profile_key
from apps.server.security import decrypt_bytes
from apps.server.servers_api import MAX_PROFILE_VERSIONS
from db.connection import db_manager
from tests.conftest import STORE, runtime_of


def capture(client, headers, fake, sandbox, name="work login", data=b"firefox-profile-tar", **body):
    fake.containers[runtime_of(sandbox["id"])].dirs["/home/zoo/.mozilla"] = data
    res = client.post(
        f"/sandboxes/{sandbox['id']}/profiles", json={"name": name, "app": "firefox", **body}, headers=headers
    )
    assert res.status_code == 201, res.text
    return res.json()


def version_files(profile_id: str) -> list[str]:
    with db_manager.session() as db:
        return [profile_key(v.id) for v in db.list_profile_versions(profile_id=profile_id)]


def test_capture_stores_an_encrypted_profile(client, alice, sandbox, fake):
    profile = capture(client, alice, fake, sandbox)
    assert profile["name"] == "work login" and profile["app"] == "firefox"
    assert profile["version"] == 1 and profile["versions"] == 1
    [key] = version_files(profile["id"])
    stored = STORE[key]
    assert stored != b"firefox-profile-tar"
    assert decrypt_bytes(stored) == b"firefox-profile-tar"
    assert [p["id"] for p in client.get("/profiles", headers=alice).json()] == [profile["id"]]


def test_capture_errors(client, alice, sandbox):
    sid = sandbox["id"]
    assert (
        client.post(f"/sandboxes/{sid}/profiles", json={"name": "x", "app": "netscape"}, headers=alice).status_code
        == 422
    )
    assert (
        client.post(f"/sandboxes/{sid}/profiles", json={"name": "x", "app": "firefox"}, headers=alice).status_code
        == 404
    )


def test_saving_again_adds_a_version(client, alice, sandbox, fake):
    first = capture(client, alice, fake, sandbox, data=b"v1")
    second = capture(client, alice, fake, sandbox, data=b"v2")
    assert second["id"] == first["id"] and second["version"] == 2 and second["versions"] == 2
    # by id, under any name
    third = capture(client, alice, fake, sandbox, name="ignored", data=b"v3", profile_id=first["id"])
    assert third["id"] == first["id"] and third["version"] == 3 and third["name"] == "work login"
    assert len(client.get("/profiles", headers=alice).json()) == 1
    versions = client.get(f"/profiles/{first['id']}/versions", headers=alice).json()
    assert [v["version"] for v in versions] == [3, 2, 1]
    assert versions[0]["sandbox_id"] == sandbox["id"] and versions[0]["size_bytes"] == 2
    # a different name is a different profile
    other = capture(client, alice, fake, sandbox, name="personal", data=b"p1")
    assert other["id"] != first["id"] and other["version"] == 1


def test_capture_into_a_profile_of_another_app_is_refused(client, alice, sandbox, fake):
    profile = capture(client, alice, fake, sandbox)
    fake.containers[runtime_of(sandbox["id"])].dirs["/home/zoo/.config/chromium"] = b"c"
    res = client.post(
        f"/sandboxes/{sandbox['id']}/profiles",
        json={"name": "x", "app": "chromium", "profile_id": profile["id"]},
        headers=alice,
    )
    assert res.status_code == 400


def test_load_picks_a_version(client, alice, sandbox, make_sandbox, fake):
    profile = capture(client, alice, fake, sandbox, data=b"v1")
    capture(client, alice, fake, sandbox, data=b"v2")
    other = make_sandbox()
    dirs = fake.containers[runtime_of(other["id"])].dirs
    assert client.post(f"/sandboxes/{other['id']}/profiles/{profile['id']}", headers=alice).status_code == 200
    assert dirs == {"/home/zoo": b"v2"}
    res = client.post(f"/sandboxes/{other['id']}/profiles/{profile['id']}?version=1", headers=alice)
    assert res.status_code == 200
    assert dirs == {"/home/zoo": b"v1"}
    res = client.post(f"/sandboxes/{other['id']}/profiles/{profile['id']}?version=9", headers=alice)
    assert res.status_code == 404


def test_load_is_refused_while_the_app_runs(client, alice, sandbox, make_sandbox, fake):
    profile = capture(client, alice, fake, sandbox)
    other = make_sandbox()
    container = fake.containers[runtime_of(other["id"])]
    container.running_apps.add("firefox")
    res = client.post(f"/sandboxes/{other['id']}/profiles/{profile['id']}", headers=alice)
    assert res.status_code == 409
    assert "Firefox is running" in res.json()["error"]["message"]
    assert container.dirs == {}
    container.running_apps.clear()
    assert client.post(f"/sandboxes/{other['id']}/profiles/{profile['id']}", headers=alice).status_code == 200
    assert container.dirs == {"/home/zoo": b"firefox-profile-tar"}


def test_load_fails_safe_when_the_check_fails(client, alice, sandbox, make_sandbox, fake, monkeypatch):
    profile = capture(client, alice, fake, sandbox)
    other = make_sandbox()

    def broken(runtime_id, app):
        raise ConnectionError("no route to host")

    monkeypatch.setattr("apps.server.servers_api.app_running", broken)
    res = client.post(f"/sandboxes/{other['id']}/profiles/{profile['id']}", headers=alice)
    assert res.status_code == 503
    assert fake.containers[runtime_of(other["id"])].dirs == {}


def test_apply_at_create_uses_the_latest_version(client, alice, sandbox, make_sandbox, fake):
    profile = capture(client, alice, fake, sandbox, data=b"v1")
    capture(client, alice, fake, sandbox, data=b"v2")
    fresh = make_sandbox(profile_ids=[profile["id"]])
    assert fake.containers[runtime_of(fresh["id"])].dirs == {"/home/zoo": b"v2"}


def test_old_versions_are_pruned(client, alice, sandbox, fake):
    profile = None
    for i in range(MAX_PROFILE_VERSIONS + 2):
        profile = capture(client, alice, fake, sandbox, data=f"v{i}".encode())
    assert profile is not None and profile["versions"] == MAX_PROFILE_VERSIONS
    versions = client.get(f"/profiles/{profile['id']}/versions", headers=alice).json()
    assert versions[-1]["version"] == 3
    assert len([k for k in STORE if k.startswith("profiles/")]) >= MAX_PROFILE_VERSIONS
    for key in version_files(profile["id"]):
        assert key in STORE


def test_delete_a_version(client, alice, sandbox, fake):
    profile = capture(client, alice, fake, sandbox, data=b"v1")
    capture(client, alice, fake, sandbox, data=b"v22")
    v2_file = version_files(profile["id"])[0]
    res = client.delete(f"/profiles/{profile['id']}/versions/2", headers=alice)
    assert res.status_code == 200
    assert res.json()["version"] == 1 and res.json()["size_bytes"] == 2
    assert v2_file not in STORE
    assert client.delete(f"/profiles/{profile['id']}/versions/1", headers=alice).status_code == 409
    assert client.delete(f"/profiles/{profile['id']}/versions/7", headers=alice).status_code == 404


def test_rename_and_delete(client, alice, sandbox, fake):
    profile = capture(client, alice, fake, sandbox)
    capture(client, alice, fake, sandbox)
    files = version_files(profile["id"])
    renamed = client.patch(f"/profiles/{profile['id']}", json={"name": "  personal "}, headers=alice).json()
    assert renamed["name"] == "personal"
    assert client.delete(f"/profiles/{profile['id']}", headers=alice).status_code == 204
    assert files and not any(f in STORE for f in files)
    assert client.get("/profiles", headers=alice).json() == []


def test_profiles_are_private(client, alice, bob, sandbox, make_sandbox, fake):
    profile = capture(client, alice, fake, sandbox)
    bobs = make_sandbox(headers=bob)
    assert client.get("/profiles", headers=bob).json() == []
    assert client.post(f"/sandboxes/{bobs['id']}/profiles/{profile['id']}", headers=bob).status_code == 404
    assert client.get(f"/profiles/{profile['id']}/versions", headers=bob).status_code == 404
    assert client.delete(f"/profiles/{profile['id']}/versions/1", headers=bob).status_code == 404
    assert client.patch(f"/profiles/{profile['id']}", json={"name": "mine"}, headers=bob).status_code == 404
    assert client.delete(f"/profiles/{profile['id']}", headers=bob).status_code == 404
    assert client.post("/sandboxes", json={"profile_ids": [profile["id"]]}, headers=bob).status_code == 404
    fake.containers[runtime_of(bobs["id"])].dirs["/home/zoo/.mozilla"] = b"bob"
    res = client.post(
        f"/sandboxes/{bobs['id']}/profiles",
        json={"name": "x", "app": "firefox", "profile_id": profile["id"]},
        headers=bob,
    )
    assert res.status_code == 404
