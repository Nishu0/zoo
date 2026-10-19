import inspect

import pytest

from apps.server.registry import TOOLS
from tests.tool_args import SAMPLE_ARGS


@pytest.mark.parametrize("name", TOOLS)
def test_sample_args_bind_on_every_platform(name):
    tool = TOOLS[name]
    for impl in (tool.fn, tool.mac, tool.win):
        inspect.signature(impl).bind("runtime-id", **SAMPLE_ARGS[name])


def test_sample_args_cover_every_tool():
    assert set(SAMPLE_ARGS) == set(TOOLS)


@pytest.mark.parametrize("name", TOOLS)
def test_vm_implementations_accept_every_linux_parameter(name):
    """MCP and REST advertise the Linux signature, so the macOS and Windows versions must accept the same names."""
    tool = TOOLS[name]
    advertised = {p.name for p in tool.params}
    for impl in (tool.mac, tool.win):
        params = inspect.signature(impl).parameters
        accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        assert accepts_kwargs or advertised <= set(params), f"{impl.__qualname__} misses {advertised - set(params)}"


def test_bad_arguments_are_rejected_before_running(client, alice, sandbox, fake):
    sid = sandbox["id"]
    missing = client.post(f"/sandboxes/{sid}/tools/click", json={"x": 1}, headers=alice)
    assert missing.status_code == 422
    unknown = client.post(f"/sandboxes/{sid}/tools/click", json={"x": 1, "y": 2, "force": True}, headers=alice)
    assert unknown.status_code == 422
    assert client.post(f"/sandboxes/{sid}/tools/teleport", json={}, headers=alice).status_code == 404
    assert fake.tool_calls("click") == []


def test_defaults_are_left_to_the_implementation(client, alice, sandbox, fake):
    client.post(f"/sandboxes/{sandbox['id']}/tools/click", json={"x": 1, "y": 2}, headers=alice)
    assert fake.tool_calls("click") == [{"x": 1, "y": 2}]
