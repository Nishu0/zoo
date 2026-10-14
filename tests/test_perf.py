import asyncio
import io
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from db.connection import db_manager
from server import security
from server.cache import Cache
from server.images import encode
from server.writer import BATCH_ROWS, writer
from tests.conftest import present, runtime_of, sql


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
    # a paused sandbox still holds what it had; resuming it hands over the current values
    assert sid in security._booted
    client.post(f"/sandboxes/{sid}/start", headers=alice)
    assert "first-value" in security._booted[sid]
    # the sandbox may still hold the value it resumed with, so that's still one to mask
    client.put(f"/sandboxes/{sid}/secrets", json={"name": "API_TOKEN", "value": "second-value"}, headers=alice)
    out = client.post(f"/sandboxes/{sid}/exec", json={"command": "echo first-value"}, headers=alice).json()
    assert out["stdout"] == "echo [redacted]"


def test_failed_tool_rows_are_written_in_the_background(client, alice, sandbox, fake):
    fake.fail_tool = "boom"
    client.post(f"/sandboxes/{sandbox['id']}/tools/click", json={"x": 1, "y": 2}, headers=alice)
    [row] = client.get(f"/sandboxes/{sandbox['id']}/executions", headers=alice).json()["data"]
    assert (row["status"], row["error_message"]) == ("failed", "boom")
    assert runtime_of(sandbox["id"])


def test_usage_rows_beyond_one_batch_are_all_written(sandbox):
    with db_manager.session() as db:
        workspace_id = present(db.get_sandbox(id=sandbox["id"])).workspace_id
    at = datetime.now(UTC)
    for _ in range(BATCH_ROWS + 3):
        writer.usage(workspace_id, sandbox["id"], "cpu_minutes", 1, at)
    writer.flush_sync()
    with db_manager.session() as db:
        rows = list(
            db.usage_report(
                workspace_id=workspace_id, since=at - timedelta(days=1), until=at + timedelta(days=1), metric=None
            )
        )
    assert [(r.metric, r.quantity) for r in rows] == [("cpu_minutes", BATCH_ROWS + 3)]


def test_a_committed_notification_drops_that_workspaces_cached_entries(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_manager.url)

    async def scenario():
        cache = Cache()
        await cache.start()
        loads = []

        async def loader(workspace_id):
            loads.append(workspace_id)
            return {"workspace_id": workspace_id}

        try:
            for workspace_id in ("a", "b", "a", "b"):
                await cache.get("workspace", workspace_id, lambda w=workspace_id: loader(w))
            assert loads == ["a", "b"]
            await asyncio.to_thread(sql, "SELECT pg_notify('zoo_event', %s)", "a")
            async with asyncio.timeout(5):
                while ("workspace", "a") in cache.entries:
                    await asyncio.sleep(0.01)
            await cache.get("workspace", "a", lambda: loader("a"))
            await cache.get("workspace", "b", lambda: loader("b"))
            assert loads == ["a", "b", "a"]
        finally:
            await cache.close()

    asyncio.run(scenario())
