"""Every registered tool must be reachable through REST, MCP and the Python SDK with the same arguments,
and all three must hand the same arguments to the runtime."""

import asyncio
import base64
import inspect

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from zoo_sdk import Zoo

from apps.server.registry import TOOLS
from tests.tool_args import SAMPLE_ARGS


@pytest.fixture
def live(live_url):
    """A user, an API key and a running desktop sandbox on the live server."""
    with httpx.Client(base_url=f"{live_url}/v1") as http:
        token = http.post("/auth/signup", json={"email": "contract@example.com", "password": "correct-horse"}).json()[
            "access_token"
        ]
        key = http.post("/api-keys", json={"name": "contract"}, headers={"Authorization": f"Bearer {token}"}).json()[
            "key"
        ]
    zoo = Zoo(api_key=key, base_url=live_url)
    sandbox = zoo.create(kind="desktop", timeout=15)
    return live_url, key, zoo, sandbox


def bound(name, args):
    """The arguments a call resolves to once defaults are filled in, which is what the runtime acts on."""
    call = inspect.signature(TOOLS[name].fn).bind("", **args)
    call.apply_defaults()
    return dict(list(call.arguments.items())[1:])


def runtime_calls(fake, name):
    calls = fake.tool_calls(name)
    fake.calls.clear()
    return calls


def test_rest_mcp_and_sdk_agree(live, fake):
    url, key, _, sandbox = live
    headers = {"Authorization": f"Bearer {key}"}

    async def mcp_calls():
        async with (
            create_mcp_http_client(headers=headers) as http,
            streamable_http_client(f"{url}/mcp/", http_client=http) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listed = {t.name: t for t in (await session.list_tools()).tools}
            results = {}
            for name in TOOLS:
                results[name] = await session.call_tool(name, {"sandbox_id": sandbox.id, **SAMPLE_ARGS[name]})
            return listed, results

    fake.calls.clear()
    listed, mcp_results = asyncio.run(mcp_calls())
    mcp_args = {name: fake.tool_calls(name) for name in TOOLS}
    fake.calls.clear()

    rest_args, sdk_args = {}, {}
    with httpx.Client(base_url=f"{url}/v1", headers=headers) as http:
        for name in TOOLS:
            res = http.post(f"/sandboxes/{sandbox.id}/tools/{name}", json=SAMPLE_ARGS[name])
            assert res.status_code == 200, f"REST {name}: {res.text}"
            rest_args[name] = runtime_calls(fake, name)
    for name in TOOLS:
        sandbox.tool(name, **SAMPLE_ARGS[name])
        sdk_args[name] = runtime_calls(fake, name)

    for name, tool in TOOLS.items():
        assert name in listed, f"{name} is missing from MCP"
        schema = listed[name].input_schema
        assert set(schema["properties"]) == {"sandbox_id", *(p.name for p in tool.params)}, name
        assert set(schema.get("required", [])) == {
            "sandbox_id",
            *(p.name for p in tool.params if p.default is p.empty),
        }, name
        assert not mcp_results[name].is_error, f"MCP {name}: {mcp_results[name].content}"
        expected = [bound(name, SAMPLE_ARGS[name])]
        assert [bound(name, a) for a in mcp_args[name]] == expected, f"MCP {name}"
        assert [bound(name, a) for a in rest_args[name]] == expected, f"REST {name}"
        assert [bound(name, a) for a in sdk_args[name]] == expected, f"SDK {name}"


def test_rest_tool_list_matches_registry(live):
    _, _, zoo, _ = live
    advertised = {t["name"]: t for t in zoo.tools()}
    assert set(advertised) == set(TOOLS)
    for name, tool in TOOLS.items():
        assert set(advertised[name]["params"]) == {p.name for p in tool.params}, name
        assert advertised[name]["permission"] == f"{tool.permission}.{tool.action}"


def test_sdk_helpers(live, fake):
    _, _, zoo, sandbox = live
    assert sandbox.exec("echo hi")["stdout"] == "echo hi"
    assert base64.b64encode(sandbox.screenshot())
    sandbox.hotkey("ctrl", "c")
    assert fake.tool_calls("hotkey")[-1] == {"keys": ["ctrl", "c"]}
    assert [s.id for s in zoo.sandboxes()] == [sandbox.id]
    sandbox.set_permission("shell", "exec", "deny")
    with pytest.raises(Exception, match="403"):
        sandbox.exec("id")
    sandbox.delete()
    assert zoo.sandboxes() == []


def test_sdk_snapshots(live, fake):
    _, _, _, sandbox = live
    snap = sandbox.snapshot("before")
    assert snap["state"] == "ready" and [s["id"] for s in sandbox.snapshots()] == [snap["id"]]
    with pytest.raises(Exception, match="409"):
        sandbox.restore_snapshot(snap["id"])
    sandbox.stop()
    assert sandbox.restore_snapshot(snap["id"]).status == "stopped"
    assert ("restore_snapshot", sandbox.id, {"snapshot_id": snap["id"]}) in fake.calls
    sandbox.delete_snapshot(snap["id"])
    assert sandbox.snapshots() == []


def test_mcp_rejects_missing_key(live):
    url, _, _, sandbox = live

    async def call():
        async with (
            streamable_http_client(f"{url}/mcp/") as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await session.call_tool("screenshot", {"sandbox_id": sandbox.id})

    result = asyncio.run(call())
    assert result.is_error
    assert "unauthorized" in result.content[0].text


def test_mcp_reports_why_a_call_failed(live):
    url, key, _, sandbox = live
    sandbox.set_permission("shell", "exec", "deny")

    async def call():
        async with (
            create_mcp_http_client(headers={"Authorization": f"Bearer {key}"}) as http,
            streamable_http_client(f"{url}/mcp/", http_client=http) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await session.call_tool("execute_command", {"sandbox_id": sandbox.id, "command": "id"})

    result = asyncio.run(call())
    assert result.is_error
    assert "shell.exec is denied for this sandbox" in result.content[0].text
