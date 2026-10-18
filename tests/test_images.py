from tests.conftest import runtime_of


def image_of(fake, sandbox_id: str) -> str:
    return fake.containers[runtime_of(sandbox_id)].image


def test_sandboxes_keep_their_image_across_upgrades(client, alice, sandbox, fake, monkeypatch):
    sid = sandbox["id"]
    old = image_of(fake, sid)
    assert sandbox["image"] == old and not sandbox["image_outdated"]

    monkeypatch.setattr("server.runtime.IMAGE", "docker.io/chann44/zoo-sandbox-desktop:9.9.9")
    assert client.get(f"/sandboxes/{sid}", headers=alice).json()["image_outdated"]
    client.post(f"/sandboxes/{sid}/stop", headers=alice)
    client.post(f"/sandboxes/{sid}/start", headers=alice)
    assert image_of(fake, sid) == old


def test_restart_on_new_image(client, alice, sandbox, fake, monkeypatch):
    sid = sandbox["id"]
    monkeypatch.setattr("server.runtime.IMAGE", "docker.io/chann44/zoo-sandbox-desktop:9.9.9")
    assert client.post(f"/sandboxes/{sid}/upgrade", headers=alice).status_code == 200

    after = client.get(f"/sandboxes/{sid}", headers=alice).json()
    assert after["status"] == "running"
    assert after["image"] == "docker.io/chann44/zoo-sandbox-desktop:9.9.9" and not after["image_outdated"]
    assert image_of(fake, sid) == "docker.io/chann44/zoo-sandbox-desktop:9.9.9"
    assert sid in fake.volumes


def test_restart_on_new_image_boots_a_stopped_sandbox(client, alice, make_sandbox, fake, monkeypatch):
    code = make_sandbox("code")
    client.post(f"/sandboxes/{code['id']}/stop", headers=alice)
    monkeypatch.setattr("server.runtime.CODE_IMAGE", "docker.io/chann44/zoo-sandbox-code:9.9.9")
    res = client.post(f"/sandboxes/{code['id']}/upgrade", headers=alice)
    assert res.status_code == 200, res.text
    assert client.get(f"/sandboxes/{code['id']}", headers=alice).json()["status"] == "running"
    assert image_of(fake, code["id"]) == "docker.io/chann44/zoo-sandbox-code:9.9.9"


def test_browser_upgrade_uses_its_dedicated_image(client, alice, make_sandbox, fake, monkeypatch):
    browser = make_sandbox("browser")
    sid = browser["id"]
    before = image_of(fake, sid)
    monkeypatch.setattr("server.runtime.BROWSER_IMAGE", "docker.io/chann44/zoo-sandbox-browser:9.9.9")
    assert client.get(f"/sandboxes/{sid}", headers=alice).json()["image_outdated"]
    assert image_of(fake, sid) == before
    assert client.post(f"/sandboxes/{sid}/upgrade", headers=alice).status_code == 200
    assert image_of(fake, sid) == "docker.io/chann44/zoo-sandbox-browser:9.9.9"
    assert sid in fake.volumes
