import asyncio
import contextlib
import os
import re
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import psycopg
import pytest

# configuration is read at import time, so it has to be in place before any server module loads
WORK = Path(tempfile.mkdtemp(prefix="zoo-tests-"))
ADMIN_EMAIL = "admin@example.com"
OBJECT_STORE_ENV = {
    name: os.environ.get(name) for name in ("ZOO_OBJECT_STORE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
}
os.environ.update(
    JWT_SECRET="test-jwt-secret-that-is-at-least-32-bytes",
    ZOO_RUNTIME="docker",
    ZOO_SECRETS_KEY="test-secrets-key",
    # object storage is faked in memory (STORE below); presigning still needs a bucket to sign for
    ZOO_OBJECT_STORE="s3://zoo-test",
    AWS_ACCESS_KEY_ID="test",
    AWS_SECRET_ACCESS_KEY="test",
    # no scheduled backups from the live server tests start
    ZOO_BACKUP_INTERVAL_HOURS="0",
    ADMIN_EMAILS=ADMIN_EMAIL,
    CORS_ORIGINS="http://localhost:3000",
    ZOO_NODE_PORT="7443",
    ZOO_NODE_DIST=str(WORK / "node-dist"),
)
UNSET = (
    "ZOO_SECRETS_KEY_PREVIOUS",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "ZOO_DOMAIN",
    "ZOO_PUBLIC_IP",
    "ZOO_API_URL",
)
for name in UNSET:
    os.environ.pop(name, None)
from fastapi.testclient import TestClient

from db.connection import db_manager
from tests.fake_runtime import FakeRuntime

MIGRATIONS = Path(__file__).parent.parent / "db" / "migrations"
PASSWORD = "correct-horse"
# a Postgres server to run the suite against (e.g. postgresql://localhost/postgres): each test gets a database
# copied from a migrated template
POSTGRES = os.environ.get("ZOO_TEST_DATABASE_URL", "")
if not POSTGRES:
    raise RuntimeError(
        "ZOO_TEST_DATABASE_URL is not set: point it at a Postgres server the suite may create databases in,"
        " e.g. postgresql://zoo:zoo@localhost:5432/postgres"
    )
TEMPLATE_DB = f"zoo_test_{os.getpid()}"


def postgres_url(database: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    return urlunsplit(urlsplit(POSTGRES)._replace(path=f"/{database}"))


# the server modules read DATABASE_URL when they build; fresh_db re-points it at each test's database
os.environ["DATABASE_URL"] = postgres_url(TEMPLATE_DB)


def up_sections(directory: Path) -> list[str]:
    """The goose Up sections in order, the same schema `goose up` builds."""
    return [
        re.sub(r"^-- \+goose .*$", "", m.read_text().split("-- +goose Down")[0], flags=re.MULTILINE)
        for m in sorted(directory.glob("*.sql"))
    ]


def postgres_admin(*statements: str):
    with psycopg.connect(POSTGRES, autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement.encode())


postgres_admin(f"DROP DATABASE IF EXISTS {TEMPLATE_DB}", f"CREATE DATABASE {TEMPLATE_DB}")
with psycopg.connect(postgres_url(TEMPLATE_DB), autocommit=True) as _conn:
    for _up in up_sections(MIGRATIONS):
        _conn.execute(_up.encode())
_fake = FakeRuntime()
_patches = pytest.MonkeyPatch()
_fake.install(_patches)

from server import objects

OBJECT_STORE_FUNCTIONS = {name: getattr(objects, name) for name in ("put", "get", "delete", "keys", "listing")}

# object storage: key -> bytes, emptied before every test
STORE: dict[str, bytes] = {}


def _get(key: str) -> bytes:
    if key not in STORE:
        raise FileNotFoundError(key)
    return STORE[key]


_patches.setattr(objects, "put", STORE.__setitem__)
_patches.setattr(objects, "get", _get)
_patches.setattr(objects, "delete", lambda key: STORE.pop(key, None))
_patches.setattr(objects, "keys", lambda prefix: [k for k in STORE if k.startswith(prefix)])
_patches.setattr(objects, "listing", lambda prefix: [(k, len(v)) for k, v in STORE.items() if k.startswith(prefix)])

from db.async_connection import async_db
from server import gateway_client, guest, nodes, registry, sandbox_api, vault_sync
from server import server as server_module
from server.limits import LIMITS
from server.server import Server
from server.writer import writer
from tests.fake_gateway import FakeGateway

GATEWAY = FakeGateway()
for module in (gateway_client, guest, nodes, registry, sandbox_api, server_module):
    _patches.setattr(module, "client", GATEWAY)


class DirectConnections:
    """asyncpg connections opened on whatever loop asks, for TestClient's per-request loops; the app's pool is
    made by its lifespan, which these tests don't run."""

    @contextlib.asynccontextmanager
    async def acquire(self):
        connection = await asyncpg.connect(db_manager.url, server_settings={"timezone": "UTC"})
        try:
            yield connection
        finally:
            await connection.close()

    async def fetch(self, statement: str, *params):
        async with self.acquire() as connection:
            return await connection.fetch(statement, *params)


_patches.setattr(async_db, "require", lambda: DirectConnections())
_patches.setattr(async_db, "fetch", lambda statement, *params: DirectConnections().fetch(statement, *params))
# the request writer flushes on a loop of its own, as it does in the API process
_writer_loop = asyncio.new_event_loop()
threading.Thread(target=_writer_loop.run_forever, daemon=True).start()
asyncio.run_coroutine_threadsafe(writer.start(), _writer_loop).result()

_server = Server()
# litellm reads the repo's .env as it imports; the test env policy wins after that too
for name in UNSET:
    os.environ.pop(name, None)
# lifecycle jobs and secret pushes run in the request that queued them, so a test sees their outcome right away
_server.sandbox_api.jobs.inline = True
vault_sync.inline = True


def pytest_unconfigure(config):
    _patches.undo()
    shutil.rmtree(WORK, ignore_errors=True)
    db_manager.close()
    postgres_admin(f"DROP DATABASE IF EXISTS {TEMPLATE_DB}_t", f"DROP DATABASE IF EXISTS {TEMPLATE_DB}")


@pytest.fixture(autouse=True)
def fresh_db():
    """Every test starts from an empty, fully migrated database and a clean fake runtime."""
    db_manager.close()
    name = f"{TEMPLATE_DB}_t"
    postgres_admin(f"DROP DATABASE IF EXISTS {name}", f"CREATE DATABASE {name} TEMPLATE {TEMPLATE_DB}")
    db_manager.init_db(url=postgres_url(name))
    db_manager.wait()
    _fake.reset()
    GATEWAY.reset()
    if not writer.accepting:
        # a test that ran the app's lifespan shut the writer down with it
        writer.task = None
        asyncio.run_coroutine_threadsafe(writer.start(), _writer_loop).result()
    guest.hub.guests.clear()
    guest.hub.runtimes.clear()
    guest.hub.statuses.clear()
    STORE.clear()
    for limit in LIMITS:
        limit.reset()
    yield


@pytest.fixture
def fake() -> FakeRuntime:
    return _fake


@pytest.fixture
def real_object_store(monkeypatch):
    if not OBJECT_STORE_ENV["ZOO_OBJECT_STORE"]:
        pytest.skip("export ZOO_OBJECT_STORE for the Kubernetes snapshot integration")
    for name, value in OBJECT_STORE_ENV.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    for name, original in OBJECT_STORE_FUNCTIONS.items():
        monkeypatch.setattr(objects, name, original)
    objects.require()


@pytest.fixture
def runtime_requirements(monkeypatch) -> set[str]:
    from server import requirements

    names = {"docker", "kvm", "kata_runtime", "sandbox_images"}
    for name in names:
        assert name in requirements.CHECKS
        monkeypatch.setitem(requirements.CHECKS, name, lambda: None)
    return names | {"runtime"}


@pytest.fixture
def zoo() -> Server:
    return _server


@pytest.fixture
def client() -> TestClient:
    return V1Client(_server.app)


# the routes that stay outside /v1 (T-10)
UNPREFIXED = ("/healthz", "/readyz", "/embed/", "/mcp", "/guest/connect", "/internal/", "/openapi.json", "/v1/", "http")


class V1Client(TestClient):
    """Calls the API as the SDKs do: under /v1, except the routes that stay unprefixed."""

    def request(self, method, url, *args, **kwargs):
        return super().request(method, url if str(url).startswith(UNPREFIXED) else f"/v1{url}", *args, **kwargs)

    def websocket_connect(self, url, *args, **kwargs):
        return super().websocket_connect(url if url.startswith(UNPREFIXED) else f"/v1{url}", *args, **kwargs)


def signup(client: TestClient, email: str) -> dict[str, str]:
    res = client.post("/auth/signup", json={"email": email, "password": PASSWORD, "name": email.split("@")[0]})
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture
def alice(client) -> dict[str, str]:
    return signup(client, "alice@example.com")


@pytest.fixture
def bob(client) -> dict[str, str]:
    return signup(client, "bob@example.com")


@pytest.fixture
def admin(client) -> dict[str, str]:
    return signup(client, ADMIN_EMAIL)


@pytest.fixture
def make_sandbox(client, alice):
    """Creates a sandbox and returns it once the (synchronous, faked) boot has finished."""

    def make(kind: str = "desktop", headers: dict | None = None, **body) -> dict:
        headers = headers or alice
        res = client.post("/sandboxes", json={"kind": kind, **body}, headers=headers)
        assert res.status_code == 201, res.text
        return client.get(f"/sandboxes/{res.json()['id']}", headers=headers).json()

    return make


@pytest.fixture
def sandbox(make_sandbox) -> dict:
    return make_sandbox()


def member(email: str = "alice@example.com", workspace_id: str | None = None):
    """The signed-up user as a request would see them: in their personal workspace (or `workspace_id`), with their
    role there."""
    from server.auth_api import Member, personal_workspace

    with db_manager.session() as db:
        user = present(db.get_user_by_email(email=email))
        workspace_id = workspace_id or personal_workspace(user, db)
        role = present(db.get_workspace_member(workspace_id=workspace_id, user_id=user.id)).role
    return Member(**user.model_dump(), workspace_id=workspace_id, role=role)


def present[T](value: T | None) -> T:
    """The value, which the test expects to be there."""
    assert value is not None
    return value


def sql(statement: str, *params):
    """Runs SQL against the test database directly, to set up states the API can't reach (old timestamps)."""
    db_manager.execute(statement, *params)


def runtime_of(sandbox_id: str) -> str:
    with db_manager.session() as db:
        return db.get_sandbox(id=sandbox_id).runtime_id


@pytest.fixture(scope="session")
def live_url():
    """The app served over a real socket with its lifespan running, for the SDK and MCP clients. The host checks
    and the request writer are the tests' own (no Docker here; the writer runs on its own loop)."""
    import uvicorn

    async def nothing():
        return None

    # the lifespan of this server only: no host checks (no Docker here), the tests' own writer, and nothing that
    # holds a connection to the template database every test copies
    stand_in = SimpleNamespace(start=nothing, close=nothing)
    _patches.setattr(server_module, "requirements", SimpleNamespace(failures=list))
    _patches.setattr(server_module, "workers", SimpleNamespace(works=lambda: False))
    _patches.setattr(server_module, "writer", stand_in)
    _patches.setattr(server_module, "async_db", stand_in)
    _patches.setattr(server_module, "cache", stand_in)

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(_server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("test server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
