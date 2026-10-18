import base64
import json

from db.connection import db_manager
from db.generated.query import CreateServerParams
from server.auth_api import personal_workspace
from server.platforms import capabilities_of
from tests.conftest import present, runtime_of, sql
from tests.fake_runtime import PNG


def add_server(user_email: str, platform: str = "windows", name: str = "box") -> str:
    with db_manager.session() as db:
        user = present(db.get_user_by_email(email=user_email))
        server = db.create_server(
            CreateServerParams(
                id=f"{name}-{platform}",
                name=name,
                docker_url=f"node://zoo@{name}-{platform}",
                bind_address="10.0.0.2",
                created_by=user.id,
                platform=platform,
                capabilities=capabilities_of(platform),
                workspace_id=personal_workspace(user, db),
            )
        )
    return server.id


def test_create_boots_a_running_sandbox(client, alice, fake):
    res = client.post("/sandboxes", json={"name": "work", "kind": "desktop"}, headers=alice)
    assert res.status_code == 201
    assert res.json()["status"] == "provisioning"

    sandbox = client.get(f"/sandboxes/{res.json()['id']}", headers=alice).json()
    assert sandbox["status"] == "running"
    assert sandbox["name"] == "work"
    container = fake.containers[runtime_of(sandbox["id"])]
    with db_manager.session() as db:
        stored = present(db.get_sandbox(id=sandbox["id"]))
    assert json.loads(stored.config)["home_claim"] == f"zoo-home-{sandbox['id']}"
    assert container.image == "zoo-sandbox:latest"
    assert container.desktop


def test_code_and_browser_sandboxes(make_sandbox, fake):
    code = make_sandbox("code")
    assert code["kind"] == "code"
    assert fake.containers[runtime_of(code["id"])].image == "zoo-code:latest"
    assert not fake.containers[runtime_of(code["id"])].desktop

    browser = make_sandbox("browser")
    assert browser["status"] == "running"
    browser_container = fake.containers[runtime_of(browser["id"])]
    assert browser_container.image == "zoo-browser:latest"
    assert browser_container.env["ZOO_BROWSER_HOME"] == "https://duckduckgo.com"
    assert browser_container.env["ZOO_BROWSER_WAIT"] == "1"
    assert ("open_url", runtime_of(browser["id"]), {"url": "https://duckduckgo.com"}) in fake.calls


def test_failed_boot_is_reported(client, alice, fake):
    fake.fail_boot = "no space left on device"
    sandbox_id = client.post("/sandboxes", json={}, headers=alice).json()["id"]
    sandbox = client.get(f"/sandboxes/{sandbox_id}", headers=alice).json()
    assert sandbox["status"] == "failed"
    assert sandbox["error_message"] == "no space left on device"


def test_stop_start_delete(client, alice, sandbox, fake):
    sid = sandbox["id"]
    first = runtime_of(sid)

    assert client.post(f"/sandboxes/{sid}/start", headers=alice).status_code == 409

    queued = client.post(f"/sandboxes/{sid}/stop", headers=alice).json()
    assert queued["job"]["kind"] == "stop"
    # a stop pauses the container in place, and the next start resumes it
    stopped = client.get(f"/sandboxes/{sid}", headers=alice).json()
    assert stopped["status"] == "stopped" and stopped["resumable"]
    assert stopped["job"] is None
    assert fake.containers[first].paused and runtime_of(sid) == first

    started = client.post(f"/sandboxes/{sid}/start", headers=alice)
    assert started.json()["status"] == "provisioning"
    resumed = client.get(f"/sandboxes/{sid}", headers=alice).json()
    assert resumed["status"] == "running" and not resumed["resumable"]
    assert runtime_of(sid) == first and not fake.containers[first].paused

    assert client.delete(f"/sandboxes/{sid}", headers=alice).json() == {"deleted": True, "id": sid}
    assert fake.containers == {}
    assert sid not in fake.volumes
    assert client.get(f"/sandboxes/{sid}", headers=alice).status_code == 404
    assert client.get("/sandboxes", headers=alice).json() == {"data": [], "next_cursor": None}


def test_sandboxes_are_private(client, alice, bob, sandbox):
    sid = sandbox["id"]
    assert client.get("/sandboxes", headers=bob).json() == {"data": [], "next_cursor": None}
    for method, path in [
        ("get", f"/sandboxes/{sid}"),
        ("post", f"/sandboxes/{sid}/stop"),
        ("post", f"/sandboxes/{sid}/start"),
        ("delete", f"/sandboxes/{sid}"),
        ("post", f"/sandboxes/{sid}/tools/screenshot"),
        ("get", f"/sandboxes/{sid}/executions"),
        ("get", f"/sandboxes/{sid}/backup"),
    ]:
        assert client.request(method, path, headers=bob).status_code == 404, path
    assert client.get(f"/sandboxes/{sid}", headers=alice).json()["status"] == "running"


def test_tools_need_a_running_sandbox(client, alice, sandbox):
    client.post(f"/sandboxes/{sandbox['id']}/stop", headers=alice)
    res = client.post(f"/sandboxes/{sandbox['id']}/tools/screenshot", headers=alice)
    assert res.status_code == 409


def test_exec_screenshot_and_history(client, alice, sandbox):
    sid = sandbox["id"]
    res = client.post(f"/sandboxes/{sid}/exec", json={"command": "echo hi"}, headers=alice)
    assert res.json() == {"sandbox_id": sid, "exit_code": 0, "stdout": "echo hi", "stderr": "", "timed_out": False}

    shot = client.post(f"/sandboxes/{sid}/screenshot", headers=alice)
    assert shot.headers["content-type"] == "image/png"
    assert shot.content == PNG

    history = client.get(f"/sandboxes/{sid}/executions", headers=alice).json()["data"]
    assert {(e["tool_name"], e["status"]) for e in history} == {
        ("execute_command", "completed"),
        ("screenshot", "completed"),
    }


def test_tool_failures_are_recorded(client, alice, sandbox, fake):
    fake.fail_tool = "xdotool crashed"
    res = client.post(f"/sandboxes/{sandbox['id']}/tools/click", json={"x": 1, "y": 2}, headers=alice)
    # the sandbox failed, not Zoo: 502 with its (redacted) error
    assert res.status_code == 502
    assert res.json()["error"] == {
        "code": "upstream_error",
        "message": "xdotool crashed",
        "hint": None,
        "docs_url": res.json()["error"]["docs_url"],
        "request_id": res.headers["x-request-id"],
    }
    [execution] = client.get(f"/sandboxes/{sandbox['id']}/executions", headers=alice).json()["data"]
    assert execution["status"] == "failed"
    assert execution["error_message"] == "xdotool crashed"


def test_tool_list_follows_sandbox_kind(client, alice):
    desktop = {t["name"] for t in client.get("/tools", headers=alice).json()}
    code = {t["name"] for t in client.get("/tools", params={"kind": "code"}, headers=alice).json()}
    assert "click" in desktop and "click" not in code
    assert {"execute_command", "read_file", "fetch_url"} <= code


def test_kind_limits_tools(client, alice, make_sandbox):
    code = make_sandbox("code")
    res = client.post(f"/sandboxes/{code['id']}/tools/click", json={"x": 1, "y": 1}, headers=alice)
    assert res.status_code == 400
    browser = make_sandbox("browser")
    res = client.post(f"/sandboxes/{browser['id']}/tools/execute_command", json={"command": "id"}, headers=alice)
    assert res.status_code == 400


def test_backup_and_restore(client, alice, sandbox, fake):
    sid = sandbox["id"]
    assert client.post(f"/sandboxes/{sid}/restore", content=b"home-tar", headers=alice).status_code == 200
    assert fake.containers[runtime_of(sid)].home == b"home-tar"
    backup = client.get(f"/sandboxes/{sid}/backup", headers=alice)
    assert backup.content == b"home-tar"
    assert backup.headers["content-type"] == "application/x-tar"


def test_explicit_linux_server_must_exist(client, alice):
    response = client.post("/v1/sandboxes", json={"server_id": "missing"}, headers=alice)
    assert response.status_code == 404


def test_secrets_reach_the_sandbox_and_are_redacted(client, alice, sandbox, fake):
    sid = sandbox["id"]
    client.put(f"/sandboxes/{sid}/secrets", json={"name": "API_TOKEN", "value": "s3cret-value"}, headers=alice)
    client.post(f"/sandboxes/{sid}/stop", headers=alice)
    client.post(f"/sandboxes/{sid}/start", headers=alice)
    # secrets reach the guest's file, never the container's environment
    container = fake.containers[runtime_of(sid)]
    assert "API_TOKEN" not in container.env
    assert fake.secrets_of(container) == {"API_TOKEN": "s3cret-value"}

    out = client.post(f"/sandboxes/{sid}/exec", json={"command": "env"}, headers=alice).json()
    assert "API_TOKEN=[redacted]" in out["stdout"].splitlines()
    assert "ZOO_VNC_PASSWORD=[redacted]" in out["stdout"].splitlines()
    assert "s3cret-value" not in out["stdout"]


def test_a_runtime_event_stops_sandboxes_whose_container_is_gone(client, alice, sandbox, fake, zoo):
    rid = runtime_of(sandbox["id"])
    fake.containers[rid].running = False
    zoo.sandbox_api.runtime_event({"type": "die", "runtime_id": rid})
    assert client.get(f"/sandboxes/{sandbox['id']}", headers=alice).json()["status"] == "stopped"
    assert rid not in fake.containers


def test_screenshot_png_is_base64_over_the_tool_route(client, alice, sandbox):
    data = client.post(f"/sandboxes/{sandbox['id']}/tools/screenshot", headers=alice).json()
    assert base64.b64decode(data) == PNG


def test_the_sweeper_deletes_paused_sandboxes_down_to_their_volume(client, alice, make_sandbox, fake, zoo, monkeypatch):
    old, fresh, pressured = (make_sandbox() for _ in range(3))
    for sandbox in (old, fresh, pressured):
        client.post(f"/sandboxes/{sandbox['id']}/stop", headers=alice)
    first = runtime_of(old["id"])
    sql("UPDATE sandboxes SET paused_at = now() - interval '2 hours' WHERE id = %s", old["id"])
    sql("UPDATE sandboxes SET paused_at = now() - interval '1 minute' WHERE id = %s", pressured["id"])
    zoo.sandbox_api.sweep_paused()
    assert runtime_of(old["id"]) is None and first not in fake.containers
    assert runtime_of(fresh["id"]) and runtime_of(pressured["id"])

    # under memory pressure the oldest paused one on the node goes, one per sweep
    fake.pressured = {"local"}
    zoo.sandbox_api.sweep_paused()
    assert runtime_of(pressured["id"]) is None and runtime_of(fresh["id"])

    swept = client.get(f"/sandboxes/{old['id']}", headers=alice).json()
    assert swept["status"] == "stopped" and not swept["resumable"]
    # a swept sandbox starts cold on its volume
    client.post(f"/sandboxes/{old['id']}/start", headers=alice)
    assert client.get(f"/sandboxes/{old['id']}", headers=alice).json()["status"] == "running"
    assert runtime_of(old["id"]) not in (None, first) and old["id"] in fake.volumes


def test_a_stranded_create_is_booted_again(client, alice, sandbox, zoo):
    sql(
        "UPDATE sandboxes SET status = 'provisioning', runtime_id = NULL, updated_at = now() - interval '5 minutes'"
        " WHERE id = %s",
        sandbox["id"],
    )
    zoo.sandbox_api.expire()
    assert client.get(f"/sandboxes/{sandbox['id']}", headers=alice).json()["status"] == "running"
