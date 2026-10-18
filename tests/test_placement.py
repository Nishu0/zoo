import pytest
from fastapi import HTTPException

from db.connection import db_manager
from db.generated.query import CreateProfileParams
from server.errors import ZooError
from server.sandbox_api import AUTO, CreateSandboxRequest
from tests.conftest import member
from tests.test_sandboxes import add_server


@pytest.fixture
def user(alice):
    return member()


def place(zoo, user, server_id, kind="desktop"):
    with db_manager.session() as db:
        return zoo.sandbox_api.place(server_id, user, db, kind)


def create(zoo, user, kind, server_id=None):
    with db_manager.session() as db:
        return zoo.sandbox_api.create(CreateSandboxRequest(kind=kind, server_id=server_id), user, db)


@pytest.mark.parametrize("kind", ["desktop", "browser", "code"])
@pytest.mark.parametrize("server_id", [None, AUTO])
def test_linux_uses_the_configured_runtime(zoo, user, kind, server_id):
    assert place(zoo, user, server_id, kind) is None


def test_explicit_server_must_belong_to_user(zoo, user, bob):
    mine = add_server("alice@example.com", "windows")
    theirs = add_server("bob@example.com", "windows", name="other")
    assert place(zoo, user, mine, "windows") == mine
    with pytest.raises(HTTPException) as e:
        place(zoo, user, theirs, "windows")
    assert e.value.status_code == 404


def test_a_mac_cannot_host_windows_vms(zoo, user):
    mac = add_server("alice@example.com", "macos")
    with pytest.raises(HTTPException) as e:
        place(zoo, user, mac, "windows")
    assert e.value.status_code == 400


@pytest.mark.parametrize("kind", ["desktop", "browser", "code"])
def test_linux_requires_an_existing_server_and_ready_docker(zoo, user, kind):
    remote = add_server("alice@example.com", "windows")
    with pytest.raises(ZooError) as error:
        place(zoo, user, remote, kind)
    assert error.value.code == "unavailable"
    with pytest.raises(HTTPException) as missing:
        place(zoo, user, "missing", kind)
    assert missing.value.status_code == 404


def test_vms_need_a_server(zoo, user):
    for kind in ("macos", "windows"):
        with pytest.raises(HTTPException) as e:
            place(zoo, user, None, kind)
        assert e.value.status_code == 400


def test_vm_placement_balances_and_caps(zoo, user, monkeypatch):
    monkeypatch.setattr("server.windows.MAX_VMS", 1)
    a = add_server("alice@example.com", "windows", "a")
    b = add_server("alice@example.com", "windows", "b")
    first = place(zoo, user, AUTO, "windows")
    create(zoo, user, "windows", first)
    second = place(zoo, user, None, "windows")
    assert {first, second} == {a, b}
    create(zoo, user, "windows", second)
    with pytest.raises(HTTPException) as e:
        place(zoo, user, None, "windows")
    assert e.value.status_code == 409
    with pytest.raises(HTTPException) as e:
        place(zoo, user, "missing", "windows")
    assert e.value.status_code == 404


def test_vm_sandboxes_reject_profiles_from_another_os(zoo, user):
    add_server("alice@example.com", "macos")
    with db_manager.session() as db:
        db.create_profile(
            CreateProfileParams(
                id="p",
                user_id=user.id,
                name="work",
                app="firefox",
                size_bytes=0,
                encrypted=True,
                platform="linux",
                workspace_id=user.workspace_id,
            )
        )
    with db_manager.session() as db, pytest.raises(HTTPException) as e:
        zoo.sandbox_api.create(CreateSandboxRequest(kind="macos", profile_ids=["p"]), user, db)
    assert e.value.status_code == 400 and "linux" in e.value.detail
