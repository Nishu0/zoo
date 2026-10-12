import asyncio

import pytest

from db.connection import db_manager
from db.generated.query import CreateAgentRunParams
from server import agent_api, workers
from server.agent_api import RESUME_NOTE, Run, follow
from server.auth_api import personal_workspace
from server.security import decrypt_bytes
from tests import fake_model
from tests.conftest import STORE, member, present, sql
from tests.fake_model import click, say
from tests.fake_runtime import PNG


@pytest.fixture(autouse=True)
def scripted():
    fake_model.reset()
    yield
    fake_model.reset()


def ask(client, headers, sandbox_id, message="open firefox", **body):
    res = client.post(
        f"/sandboxes/{sandbox_id}/agent",
        json={"message": message, "model": fake_model.MODEL, "stream": False, **body},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()


def kinds(events):
    return [(e["type"], e.get("text")) for e in events if e["type"] not in ("usage",)]


def user_of(email="alice@example.com"):
    return member(email)


def latest_run(sandbox_id):
    with db_manager.session() as db:
        return present(db.get_latest_agent_run(sandbox_id=sandbox_id))


def test_a_scripted_run_drives_the_sandbox(client, alice, sandbox, fake):
    fake_model.reset([click(10, 20)], [say("Firefox is open.")])
    result = ask(client, alice, sandbox["id"])
    assert kinds(result["events"]) == [
        ("user", "open firefox"),
        ("action", "click 10, 20"),
        ("text", "Firefox is open."),
        ("done", None),
    ]
    assert result["events"][-1]["state"] == "succeeded"
    # the action went through the tool registry, like an API call
    assert fake.tool_calls("click") == [{"x": 10, "y": 20, "button": "left"}]
    run = latest_run(sandbox["id"])
    assert run.id == result["run_id"]
    assert (run.state, run.steps, run.tokens, run.attempts) == ("succeeded", 1, 1200, 1)
    assert run.cost == pytest.approx(0.004)
    usage = [e for e in result["events"] if e["type"] == "usage"]
    assert usage[-1]["steps"] == 1 and usage[-1]["tokens"] == 1200 and usage[-1]["max_steps"] == run.max_steps

    state = client.get(f"/sandboxes/{sandbox['id']}/agent", headers=alice).json()
    assert state["running"] is False and state["run"]["state"] == "succeeded" and state["run"]["steps"] == 1
    action = next(m for m in state["messages"] if m["kind"] == "action")
    assert action["has_screenshot"] and action["run_id"] == run.id


def test_each_action_keeps_the_screenshot_it_was_decided_on(client, alice, sandbox):
    fake_model.reset([click(1, 1)], [click(2, 2)], [say("ok")])
    ask(client, alice, sandbox["id"])
    messages = client.get(f"/sandboxes/{sandbox['id']}/agent", headers=alice).json()["messages"]
    actions = [m for m in messages if m["kind"] == "action"]
    assert len(actions) == 2
    for action in actions:
        res = client.get(f"/sandboxes/{sandbox['id']}/agent/messages/{action['id']}/screenshot", headers=alice)
        assert res.status_code == 200 and res.content == PNG and res.headers["content-type"] == "image/png"
    # stored encrypted
    run = latest_run(sandbox["id"])
    stored = [STORE[k] for k in STORE if k.startswith(f"agent/{run.id}/") and not k.endswith(".a11y.enc")]
    assert stored
    for data in stored:
        assert data != PNG and decrypt_bytes(data) == PNG
    text = next(m for m in messages if m["kind"] == "user")
    res = client.get(f"/sandboxes/{sandbox['id']}/agent/messages/{text['id']}/screenshot", headers=alice)
    assert res.status_code == 404


def test_screenshots_are_private_and_cleared_with_the_conversation(client, alice, bob, sandbox):
    fake_model.reset([click(1, 1)], [say("ok")])
    ask(client, alice, sandbox["id"])
    action = next(
        m
        for m in client.get(f"/sandboxes/{sandbox['id']}/agent", headers=alice).json()["messages"]
        if m["kind"] == "action"
    )
    url = f"/sandboxes/{sandbox['id']}/agent/messages/{action['id']}/screenshot"
    assert client.get(url, headers=bob).status_code == 404
    run = latest_run(sandbox["id"])
    assert any(k.startswith(f"agent/{run.id}/") for k in STORE)
    assert client.delete(f"/sandboxes/{sandbox['id']}/agent", headers=alice).status_code == 204
    assert not any(k.startswith(f"agent/{run.id}/") for k in STORE)
    assert client.get(url, headers=alice).status_code == 404


def test_the_sweep_removes_screenshots_of_deleted_runs(zoo):
    STORE["agent/gone-run/a.bin"] = b"x"
    zoo.agent_api.sweep()
    assert not STORE


def test_step_limit(client, alice, sandbox):
    client.put("/agent/settings", json={"provider": "anthropic", "model": "x", "max_steps": 2}, headers=alice)
    fake_model.reset([click(1, 1)], [click(2, 2)], [click(3, 3)], [say("never")])
    events = ask(client, alice, sandbox["id"])["events"]
    assert ("error", "stopped after 2 actions") in kinds(events)
    run = latest_run(sandbox["id"])
    assert (run.state, run.steps, run.max_steps) == ("failed", 2, 2)


def test_token_limit(client, alice, sandbox):
    client.put("/agent/settings", json={"provider": "anthropic", "model": "x", "max_tokens": 1000}, headers=alice)
    fake_model.reset([click(1, 1)], [click(2, 2)], [click(3, 3)])
    events = ask(client, alice, sandbox["id"])["events"]
    assert ("error", "stopped after 1,200 tokens (the limit is 1,000)") in kinds(events)
    assert latest_run(sandbox["id"]).state == "failed"


def test_wall_clock_limit(client, alice, sandbox, monkeypatch):
    monkeypatch.setattr(agent_api, "MAX_SECONDS", 1)
    fake_model.reset(30)
    events = ask(client, alice, sandbox["id"])["events"]
    assert ("error", "stopped after 1 seconds") in kinds(events)
    run = latest_run(sandbox["id"])
    assert run.state == "failed" and run.error == "stopped after 1 seconds"


def test_limits_are_capped_by_the_server(client, alice, monkeypatch):
    res = client.put(
        "/agent/settings",
        json={"provider": "anthropic", "model": "x", "max_steps": 10**6, "max_seconds": 10**7, "max_tokens": 10**9},
        headers=alice,
    ).json()
    assert res["limits"] == res["caps"]
    res = client.put(
        "/agent/settings", json={"provider": "anthropic", "model": "x", "max_seconds": 600}, headers=alice
    ).json()
    assert res["limits"]["max_seconds"] == 600 and res["limits"]["max_steps"] == agent_api.MAX_STEPS


def test_one_run_per_sandbox(client, alice, sandbox, zoo):
    fake_model.reset(30)
    user = user_of()

    async def scenario():
        run = zoo.agent_api.start(user, sandbox["id"], "first", "web", fake_model.MODEL)
        await asyncio.sleep(0)
        with pytest.raises(Exception) as busy:
            zoo.agent_api.start(user, sandbox["id"], "second", "web", fake_model.MODEL)
        assert getattr(busy.value, "status_code", None) == 409
        assert zoo.agent_api.stop(sandbox["id"])
        events = [e async for e in run.stream()]
        assert events[-1] == {"type": "done", "state": "cancelled"}

    asyncio.run(scenario())
    assert latest_run(sandbox["id"]).state == "cancelled"


def test_an_interrupted_run_resumes_where_it_left_off(client, alice, sandbox, zoo):
    """A worker dies mid-run (no graceful shutdown): its heartbeat goes stale, and the next worker resumes it."""
    with db_manager.session() as db:
        run = present(
            db.create_agent_run(
                CreateAgentRunParams(
                    id="run-1",
                    sandbox_id=sandbox["id"],
                    user_id=user_of().id,
                    source="web",
                    model=fake_model.MODEL,
                    max_steps=50,
                    max_seconds=600,
                    max_tokens=10**6,
                    max_attempts=3,
                    provider=None,
                    api_base=None,
                )
            )
        )
        zoo.agent_api.record(db, sandbox["id"], run.id, "user", "open firefox", "web")
        db.claim_agent_run(worker="dead-worker", id=run.id)
        zoo.agent_api.record(db, sandbox["id"], run.id, "text", "Opening the menu.", "web")
    sql("UPDATE agent_runs SET heartbeat_at = now() - interval '5 minutes'")

    fake_model.reset([click(5, 5)], [say("Firefox is open.")])

    async def scenario():
        zoo.agent_api.recover()
        zoo.agent_api.claim_queued()
        handle = zoo.agent_api.runs[sandbox["id"]]
        return [e async for e in handle.stream()]

    events = asyncio.run(scenario())
    assert kinds(events) == [
        ("user", "open firefox"),
        ("text", "Opening the menu."),
        ("status", "Resumed after a server restart."),
        ("action", "click 5, 5"),
        ("text", "Firefox is open."),
        ("done", None),
    ]
    # the model got the conversation so far and was told to carry on
    first_call = fake_model.seen[0]
    contents = [m.get("content") for m in first_call if m.get("role") == "user"]
    assert "open firefox" in str(contents) and RESUME_NOTE in str(contents)
    run = latest_run(sandbox["id"])
    assert (run.state, run.attempts) == ("succeeded", 2)


def test_a_run_that_keeps_getting_interrupted_fails(client, alice, sandbox, zoo):
    with db_manager.session() as db:
        run = present(
            db.create_agent_run(
                CreateAgentRunParams(
                    id="run-2",
                    sandbox_id=sandbox["id"],
                    user_id=user_of().id,
                    source="slack",
                    model=None,
                    max_steps=50,
                    max_seconds=600,
                    max_tokens=10**6,
                    max_attempts=1,
                    provider=None,
                    api_base=None,
                )
            )
        )
        db.claim_agent_run(worker="dead-worker", id=run.id)
    sql("UPDATE agent_runs SET heartbeat_at = now() - interval '5 minutes'")
    zoo.agent_api.recover()
    run = latest_run(sandbox["id"])
    assert run.state == "failed" and "interrupted 1 times" in (run.error or "")
    messages = client.get(f"/sandboxes/{sandbox['id']}/agent", headers=alice).json()["messages"]
    assert messages[-1]["kind"] == "error"


def test_a_worker_that_lost_its_run_lets_go(client, alice, sandbox, zoo):
    fake_model.reset(30)
    user = user_of()

    async def scenario():
        run = zoo.agent_api.start(user, sandbox["id"], "work", "web", fake_model.MODEL)
        await asyncio.sleep(0.05)
        # another worker took it over after this one looked dead
        sql("UPDATE agent_runs SET worker = 'other' WHERE id = ?", run.id)
        zoo.agent_api.heartbeat()
        assert run.task is not None
        await asyncio.wait_for(asyncio.gather(run.task, return_exceptions=True), 5)

    # a stream of it would go on following the run on the other worker
    asyncio.run(scenario())
    run = latest_run(sandbox["id"])
    # left as the other worker has it: not finished, not requeued
    assert run.state == "running" and run.worker == "other"
    assert zoo.agent_api.runs == {}


def test_stop_reaches_a_run_on_another_worker(client, alice, sandbox, zoo):
    with db_manager.session() as db:
        run = present(
            db.create_agent_run(
                CreateAgentRunParams(
                    id="run-3",
                    sandbox_id=sandbox["id"],
                    user_id=user_of().id,
                    source="web",
                    model=None,
                    max_steps=5,
                    max_seconds=60,
                    max_tokens=1000,
                    max_attempts=3,
                    provider=None,
                    api_base=None,
                )
            )
        )
        db.claim_agent_run(worker="other", id=run.id)
    assert client.get(f"/sandboxes/{sandbox['id']}/agent", headers=alice).json()["running"] is True
    assert client.post(f"/sandboxes/{sandbox['id']}/agent/stop", headers=alice).status_code == 204
    with db_manager.session() as db:
        assert db.heartbeat_agent_run(id="run-3", worker="other") is not None


def test_stopping_a_queued_run(client, alice, sandbox, zoo):
    with db_manager.session() as db:
        db.create_agent_run(
            CreateAgentRunParams(
                id="run-4",
                sandbox_id=sandbox["id"],
                user_id=user_of().id,
                source="web",
                model=None,
                max_steps=5,
                max_seconds=60,
                max_tokens=1000,
                max_attempts=3,
                provider=None,
                api_base=None,
            )
        )
    assert zoo.agent_api.stop(sandbox["id"]) is True
    assert latest_run(sandbox["id"]).state == "cancelled"
    assert zoo.agent_api.stop(sandbox["id"]) is False


def test_api_replicas_queue_runs_for_a_worker(client, alice, sandbox, zoo, monkeypatch):
    monkeypatch.setattr(workers, "ROLE", "api")
    fake_model.reset([click(7, 7)], [say("done")])
    user = user_of()

    async def scenario():
        run = zoo.agent_api.start(user, sandbox["id"], "click it", "web", fake_model.MODEL)
        assert latest_run(sandbox["id"]).state == "queued"
        # a viewer on the API replica follows the database while a worker runs it
        follower = asyncio.create_task(collect(run))
        await asyncio.sleep(0.1)
        monkeypatch.setattr(workers, "ROLE", "worker")
        zoo.agent_api.claim_queued()
        return await asyncio.wait_for(follower, 10)

    async def collect(run):
        return [e async for e in run.stream()]

    events = asyncio.run(scenario())
    assert kinds(events) == [("user", "click it"), ("action", "click 7, 7"), ("text", "done"), ("done", None)]
    assert events[-1]["state"] == "succeeded"


def test_follow_streams_a_finished_run_from_the_database(client, alice, sandbox, zoo):
    fake_model.reset([say("hi")])
    run_id = ask(client, alice, sandbox["id"])["run_id"]

    async def scenario():
        return [e async for e in Run(id=run_id, sandbox_id=sandbox["id"], source="web").stream()]

    events = asyncio.run(scenario())
    assert kinds(events) == [("user", "open firefox"), ("text", "hi"), ("done", None)]
    assert asyncio.run(anext_all(follow(run_id)))[-1]["state"] == "succeeded"


async def anext_all(stream):
    return [e async for e in stream]


def test_code_sandboxes_have_no_agent(client, alice, make_sandbox):
    code = make_sandbox(kind="code")
    res = client.post(f"/sandboxes/{code['id']}/agent", json={"message": "hi", "stream": False}, headers=alice)
    assert res.status_code == 400


def test_workspace_settings_take_the_key_from_the_vault(client, alice, bob, zoo):
    res = client.put(
        "/agent/settings",
        json={"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": "sk-ant-first"},
        headers=alice,
    ).json()
    assert res["has_api_key"] and res["api_key_secret"]["name"] == "AGENT_ANTHROPIC_API_KEY"
    secret_id = res["api_key_secret"]["id"]
    # typing a new key rotates the same vault secret
    res = client.put(
        "/agent/settings",
        json={"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": "sk-ant-second"},
        headers=alice,
    ).json()
    assert res["api_key_secret"]["id"] == secret_id
    with db_manager.session() as db:
        config = zoo.agent_api.config(personal_workspace(user_of(), db), db)
    assert config.model == "anthropic/claude-sonnet-5-5" and config.api_key == "sk-ant-second"

    # or pick any vault secret
    other = client.post("/vault/secrets", json={"name": "MY_KEY", "value": "sk-mine"}, headers=alice).json()
    my_key = next(s for s in other if s["name"] == "MY_KEY")
    res = client.put(
        "/agent/settings",
        json={"provider": "anthropic", "model": "m", "api_key_secret_id": my_key["id"]},
        headers=alice,
    ).json()
    assert res["api_key_secret"] == {"id": my_key["id"], "name": "MY_KEY"}
    # leaving the key out keeps it; switching provider drops it
    res = client.put("/agent/settings", json={"provider": "anthropic", "model": "m2"}, headers=alice).json()
    assert res["api_key_secret"]["id"] == my_key["id"]
    res = client.put("/agent/settings", json={"provider": "openai", "model": "x"}, headers=alice).json()
    assert res["api_key_secret"] is None
    # someone else's secret can't be used
    res = client.put(
        "/agent/settings",
        json={"provider": "anthropic", "model": "m", "api_key_secret_id": my_key["id"]},
        headers=bob,
    )
    assert res.status_code == 404
    # deleting the secret leaves the settings without a key
    client.put(
        "/agent/settings",
        json={"provider": "anthropic", "model": "m", "api_key_secret_id": my_key["id"]},
        headers=alice,
    )
    client.delete(f"/vault/secrets/{my_key['id']}", headers=alice)
    assert client.get("/agent/settings", headers=alice).json()["has_api_key"] is False
    assert client.delete("/agent/settings", headers=alice).json()["provider"] is None
