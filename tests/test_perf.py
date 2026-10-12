import io

import pytest
from PIL import Image

from server import security
from server.images import encode
from tests.conftest import runtime_of
from tests.test_sandboxes import add_server


def png(width=40, height=20) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(out, format="PNG")
    return out.getvalue()


def test_encode_formats_and_scale():
    original = png()
    assert encode(original) is original
    webp = Image.open(io.BytesIO(encode(original, "webp", 0.5)))
    assert (webp.format, webp.size) == ("WEBP", (20, 10))
    assert Image.open(io.BytesIO(encode(original, "jpeg"))).format == "JPEG"
    with pytest.raises(ValueError):
        encode(original, "gif")
    with pytest.raises(ValueError):
        encode(original, "png", 2)


def test_screenshot_route_takes_a_format(client, alice, sandbox, fake):
    res = client.post(f"/sandboxes/{sandbox['id']}/screenshot", params={"format": "webp", "scale": 0.5}, headers=alice)
    assert res.headers["content-type"] == "image/webp"
    assert fake.tool_calls("screenshot")[-1] == {"format": "webp", "scale": 0.5, "quality": 80}
    assert (
        client.post(f"/sandboxes/{sandbox['id']}/screenshot", params={"format": "gif"}, headers=alice).status_code
        == 422
    )


def test_running_sandbox_is_redacted_against_its_boot_secrets(client, alice, sandbox):
    sid = sandbox["id"]
    client.put(f"/sandboxes/{sid}/secrets", json={"name": "API_TOKEN", "value": "first-value"}, headers=alice)
    client.post(f"/sandboxes/{sid}/stop", headers=alice)
    assert sid not in security._booted
    client.post(f"/sandboxes/{sid}/start", headers=alice)
    assert "first-value" in security._booted[sid]
    # the container keeps the value it booted with, so that's still the one to mask
    client.put(f"/sandboxes/{sid}/secrets", json={"name": "API_TOKEN", "value": "second-value"}, headers=alice)
    out = client.post(f"/sandboxes/{sid}/exec", json={"command": "echo first-value"}, headers=alice).json()
    assert out["stdout"] == "echo [redacted]"


def test_failed_tool_rows_are_written_in_the_background(client, alice, sandbox, fake):
    fake.fail_tool = "boom"
    client.post(f"/sandboxes/{sandbox['id']}/tools/click", json={"x": 1, "y": 2}, headers=alice)
    [row] = client.get(f"/sandboxes/{sandbox['id']}/executions", headers=alice).json()["data"]
    assert (row["status"], row["error_message"]) == ("failed", "boom")
    assert runtime_of(sandbox["id"])


def test_prepull_skips_servers_without_linux(alice, fake):
    from db.connection import db_manager
    from server.servers_api import prepull_images

    add_server("alice@example.com", "linux", "a")
    add_server("alice@example.com", "macos", "b")
    add_server("alice@example.com", "macos", "c", capabilities="macos,linux")
    with db_manager.session() as db:
        prepull_images(list(db.list_all_servers())).join()
    assert sorted(c[1] for c in fake.calls if c[0] == "prepull") == ["a-linux", "c-macos"]
