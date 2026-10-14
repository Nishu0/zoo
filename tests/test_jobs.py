import asyncio
from types import SimpleNamespace

import pytest

from db.connection import db_manager
from server import docker, health, runtime
from tests.conftest import sql


@pytest.fixture
def queued(zoo, monkeypatch):
    """Jobs wait in the queue until the test runs them, as they would between worker ticks."""
    jobs = zoo.sandbox_api.jobs
    monkeypatch.setattr(jobs, "inline", False)
    yield jobs
    jobs.live.clear()
    jobs.closing = False


def jobs_of(sandbox_id: str) -> list:
    with db_manager.session() as db:
        return [
            j
            for state in ("queued", "running", "succeeded", "failed", "cancelled")
            for j in db.list_jobs_by_state(state=state)
        ]


def job(sandbox_id: str, kind: str):
    [found] = [j for j in jobs_of(sandbox_id) if j.sandbox_id == sandbox_id and j.kind == kind]
    return found


def age(job_id: str, seconds: int):
    """Moves a job back in time so its deadline has passed."""
    sql(
        f"UPDATE jobs SET created_at = now() - interval '{seconds} seconds', "
        "deadline = now() - interval '1 second' WHERE id = ?",
        job_id,
    )


def test_boot_retries_a_transient_failure(client, alice, fake):
    fake.flaky["run_container"] = 1
    sandbox_id = client.post("/sandboxes", json={}, headers=alice).json()["id"]
    sandbox = client.get(f"/sandboxes/{sandbox_id}", headers=alice).json()
    assert sandbox["status"] == "running"
    assert job(sandbox_id, "boot").attempts == 2


def test_boot_gives_up_with_the_reason_and_can_be_retried(client, alice, fake):
    fake.fail_boot = "image zoo-sandbox:latest not found"
    sandbox_id = client.post("/sandboxes", json={}, headers=alice).json()["id"]
    sandbox = client.get(f"/sandboxes/{sandbox_id}", headers=alice).json()
    assert sandbox["status"] == "failed"
    assert sandbox["error_message"] == "image zoo-sandbox:latest not found"
    boot = job(sandbox_id, "boot")
    assert (boot.state, boot.attempts, boot.last_error) == ("failed", 3, "image zoo-sandbox:latest not found")

    fake.fail_boot = None
    assert client.post(f"/sandboxes/{sandbox_id}/start", headers=alice).status_code == 200
    assert client.get(f"/sandboxes/{sandbox_id}", headers=alice).json()["status"] == "running"


def test_boot_deadlines(client, alice, queued):
    sandbox_id = client.post("/sandboxes", json={}, headers=alice).json()["id"]
    boot = job(sandbox_id, "boot")
    span = boot.deadline - boot.created_at
    assert 179 <= span.total_seconds() <= 181
    from server.sandbox_api import boot_deadline

    assert boot_deadline("macos") == boot_deadline("windows") == 600


def test_provisioning_times_out_with_a_reason(client, alice, queued, fake):
    res = client.post("/sandboxes", json={}, headers=alice).json()
    assert res["job"]["kind"] == "boot"
    age(job(res["id"], "boot").id, 181)
    queued.expire()
    sandbox = client.get(f"/sandboxes/{res['id']}", headers=alice).json()
    assert sandbox["status"] == "failed"
    assert sandbox["error_message"] == "timed out after 3 minutes"
    assert sandbox["job"] is None


def test_a_boot_that_lost_its_job_cleans_up(client, alice, queued, fake):
    sandbox_id = client.post("/sandboxes", json={}, headers=alice).json()["id"]
    claimed = queued.claim(job(sandbox_id, "boot"))
    age(claimed.id, 181)
    queued.expire()
    # the slow thread finishes after the deadline: its container must not leak or flip the sandbox to running
    queued.execute(claimed)
    assert fake.containers == {}
    assert client.get(f"/sandboxes/{sandbox_id}", headers=alice).json()["status"] == "failed"


def test_jobs_interrupted_by_a_crash_resume(client, alice, queued, fake):
    sandbox_id = client.post("/sandboxes", json={}, headers=alice).json()["id"]
    claimed = queued.claim(job(sandbox_id, "boot"))
    queued.live.clear()  # the process died mid-boot
    queued.recover()
    resumed = job(sandbox_id, "boot")
    assert (resumed.state, resumed.attempts, resumed.last_error) == (
        "queued",
        claimed.attempts - 1,
        "interrupted by a restart",
    )
    queued.drain()
    assert client.get(f"/sandboxes/{sandbox_id}", headers=alice).json()["status"] == "running"


def test_shutdown_requeues_running_jobs(client, alice, queued):
    sandbox_id = client.post("/sandboxes", json={}, headers=alice).json()["id"]
    queued.claim(job(sandbox_id, "boot"))
    asyncio.run(queued.shutdown(timeout=0))
    assert job(sandbox_id, "boot").state == "queued"


def test_delete_is_queued_and_retries_ssh_failures(client, alice, sandbox, queued, fake):
    sid = sandbox["id"]
    fake.flaky["remove_volume"] = 2
    assert client.delete(f"/sandboxes/{sid}", headers=alice).json() == {"deleted": True, "id": sid}
    pending = client.get(f"/sandboxes/{sid}", headers=alice).json()
    assert (pending["status"], pending["job"]["kind"]) == ("deleting", "delete")
    queued.drain()
    assert client.get(f"/sandboxes/{sid}", headers=alice).status_code == 404
    assert job(sid, "delete").attempts == 3
    assert sid not in fake.volumes


def test_delete_that_keeps_failing_can_be_retried(client, alice, sandbox, fake):
    sid = sandbox["id"]
    fake.flaky["remove_volume"] = 5
    client.delete(f"/sandboxes/{sid}", headers=alice)
    failed = client.get(f"/sandboxes/{sid}", headers=alice).json()
    assert failed["status"] == "failed"
    assert failed["error_message"] == "delete failed: remove_volume: connection reset by peer"
    client.delete(f"/sandboxes/{sid}", headers=alice)
    assert client.get(f"/sandboxes/{sid}", headers=alice).status_code == 404


def test_stop_drops_a_boot_that_has_not_started(client, alice, queued, fake):
    sandbox_id = client.post("/sandboxes", json={}, headers=alice).json()["id"]
    client.post(f"/sandboxes/{sandbox_id}/stop", headers=alice)
    queued.drain()
    assert client.get(f"/sandboxes/{sandbox_id}", headers=alice).json()["status"] == "stopped"
    assert job(sandbox_id, "boot").state == "cancelled"
    assert fake.containers == {}


def test_busy_sandboxes_refuse_other_lifecycle_work(client, alice, sandbox, queued, monkeypatch):
    sid = sandbox["id"]
    client.post(f"/sandboxes/{sid}/stop", headers=alice)
    queued.drain()
    monkeypatch.setattr(runtime, "NAME", "kube")
    moved = []
    monkeypatch.setattr(runtime, "move_volume", lambda sandbox_id, node: moved.append((sandbox_id, node)))
    response = client.post(f"/v1/sandboxes/{sid}/move", json={"node_name": "node-b"}, headers=alice)
    assert response.status_code == 200
    res = client.post(f"/v1/sandboxes/{sid}/start", headers=alice)
    assert (res.status_code, res.json()["error"]["message"]) == (409, "sandbox is busy: move in progress")
    queued.drain()
    assert moved == [(sid, "node-b")]
    assert client.get(f"/v1/sandboxes/{sid}", headers=alice).json()["server_id"] is None


def test_boot_adopts_the_container_an_earlier_attempt_started():
    class Container:
        def __init__(self, status, sandbox, image, runtime=docker.RUNTIME):
            self.status, self.labels, self.attrs, self.removed = (
                status,
                {"zoo.sandbox": sandbox},
                {"Config": {"Image": image}, "HostConfig": {"Runtime": runtime}},
                False,
            )

        def remove(self, force):
            self.removed = True

    def client(found):
        return SimpleNamespace(containers=SimpleNamespace(get=lambda name: found))

    mine = Container("running", "sb1", "zoo-sandbox:latest")
    assert docker.adoptable(client(mine), "zoo-sandbox-sb1", "zoo-sandbox:latest", "sb1") is mine
    for stale in (
        Container("exited", "sb1", "zoo-sandbox:latest"),
        Container("running", "sb1", "zoo-sandbox:old"),
        Container("running", "other", "zoo-sandbox:latest"),
        Container("running", "sb1", "zoo-sandbox:latest", runtime="runc"),
    ):
        assert docker.adoptable(client(stale), "zoo-sandbox-sb1", "zoo-sandbox:latest", "sb1") is None
        assert stale.removed


def test_health_probes(client, alice, monkeypatch, runtime_requirements):
    assert client.get("/healthz").json() == {"status": "ok"}
    ready = client.get("/readyz")
    assert ready.status_code == 503 and ready.json()["checks"]["api_url"].startswith("ZOO_API_URL is not set")
    monkeypatch.setenv("ZOO_API_URL", "http://zoo.test")
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert set(ready.json()["checks"].values()) == {"ok"}
    assert set(ready.json()["checks"]) == runtime_requirements | {
        "database",
        "object_store",
        "jwt_secret",
        "secrets_key",
        "api_url",
        "gateway",
    }

    from tests.test_sandboxes import add_server

    add_server("alice@example.com")

    def down(server):
        raise ConnectionError("No route to host")

    monkeypatch.setattr(health, "host_requirements", down)
    ready = client.get("/readyz")
    assert ready.status_code == 503
    assert ready.json()["checks"]["server:box"] == "No route to host"


def test_the_worker_loop_runs_queued_jobs(client, alice, queued):
    sandbox_id = client.post("/sandboxes", json={}, headers=alice).json()["id"]

    async def scenario():
        worker = asyncio.create_task(queued.run())
        for _ in range(100):
            if job(sandbox_id, "boot").state == "succeeded":
                break
            await asyncio.sleep(0.05)
        await queued.shutdown()
        worker.cancel()

    asyncio.run(scenario())
    assert client.get(f"/sandboxes/{sandbox_id}", headers=alice).json()["status"] == "running"
