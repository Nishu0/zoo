"""Portable archives cross Docker transport; object storage stays on the control plane."""

import io
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from server import docker, remote_snapshots
from server.errors import ZooError


@pytest.fixture
def relay(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    store = {}
    helpers = []

    def helper(volumes):
        found = SimpleNamespace(id="helper", start=Mock(), remove=Mock())

        def put_archive(destination, source):
            assert destination == "/to"
            assert hasattr(source, "read")  # SDK streams the file, no giant bytes value.
            with tarfile.open(fileobj=source, mode="r:") as archive:
                archive.extractall(home, filter="data")
            return True

        found.put_archive = put_archive
        helpers.append(found)
        return found

    def check(found, script):
        if script.startswith("du"):
            return str(sum(p.stat().st_size for p in home.rglob("*") if p.is_file() and not p.is_symlink()))
        for path in home.iterdir():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        return ""

    def stream(*args, **kwargs):
        assert kwargs == {"stream": True, "demux": True}
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            archive.add(home, arcname=".")
        data = buffer.getvalue()
        return iter((data[index : index + 17], None) for index in range(0, len(data), 17))

    api = SimpleNamespace(
        exec_create=Mock(return_value={"Id": "exec"}),
        exec_start=stream,
        exec_inspect=Mock(return_value={"ExitCode": 0}),
    )
    monkeypatch.setattr(docker, "helper", helper)
    monkeypatch.setattr(docker, "helper_check", check)
    monkeypatch.setattr(docker, "docker_client", SimpleNamespace(api=api))
    monkeypatch.setattr(docker, "HOME_PART_BYTES", 64)
    monkeypatch.setattr(
        remote_snapshots.objects,
        "delete_prefix",
        lambda prefix: [store.pop(k) for k in list(store) if k.startswith(prefix)],
    )
    monkeypatch.setattr(remote_snapshots.objects, "keys", lambda prefix: [k for k in store if k.startswith(prefix)])
    monkeypatch.setattr(
        remote_snapshots.objects, "put_file", lambda key, path: store.__setitem__(key, Path(path).read_bytes())
    )
    monkeypatch.setattr(remote_snapshots.objects, "get_file", lambda key, path: Path(path).write_bytes(store[key]))
    return home, store, helpers, api


def test_snapshot_restore_round_trip_across_numbered_parts(relay):
    home, store, helpers, _ = relay
    (home / ".config").mkdir()
    payload = b"private profile content" * 100
    target = home / ".config" / "profile"
    target.write_bytes(payload)
    target.chmod(0o640)
    (home / "link").symlink_to(".config/profile")
    assert remote_snapshots.snapshot("sandbox", "snapshot") == len(payload)
    assert len(store) > 1
    assert all(len(part) <= 64 for part in store.values())
    target.write_bytes(b"changed")
    (home / "extra").write_text("remove me")
    remote_snapshots.restore_snapshot("sandbox", "snapshot")
    assert target.read_bytes() == payload
    assert target.stat().st_mode & 0o777 == 0o640
    assert (home / "link").is_symlink()
    assert not (home / "extra").exists()
    assert all(found.remove.call_args.kwargs == {"force": True} for found in helpers)


def test_failed_archive_removes_partial_objects_and_helper(relay):
    home, store, helpers, api = relay
    (home / "data").write_text("data")
    api.exec_inspect.return_value = {"ExitCode": 2}
    with pytest.raises(RuntimeError, match="remote snapshot archive failed"):
        remote_snapshots.snapshot("sandbox", "snapshot")
    assert not store
    helpers[0].remove.assert_called_once_with(force=True)


def test_missing_numbered_part_preserves_existing_home(relay):
    home, store, helpers, _ = relay
    (home / "data").write_text("preserve me")
    store["snapshots/snapshot/home.0001"] = b"incomplete"
    with pytest.raises(ZooError, match="incomplete"):
        remote_snapshots.restore_snapshot("sandbox", "snapshot")
    assert (home / "data").read_text() == "preserve me"
    assert not helpers


def test_corrupt_download_preserves_existing_home(relay):
    home, store, helpers, _ = relay
    (home / "data").write_text("preserve me")
    store["snapshots/snapshot/home.0000"] = b"bad gzip"
    with pytest.raises(OSError):
        remote_snapshots.restore_snapshot("sandbox", "snapshot")
    assert (home / "data").read_text() == "preserve me"
    assert not helpers


@pytest.mark.e2e
def test_real_docker_archive_relay(monkeypatch, tmp_path):
    import os
    import uuid

    import docker as sdk
    from docker.errors import NotFound as DockerNotFound

    engine_socket = os.environ.get("ZOO_TEST_DOCKER_SOCKET")
    if not engine_socket:
        pytest.skip("set ZOO_TEST_DOCKER_SOCKET to exercise real Docker snapshot archives")
    image = os.environ.get("ZOO_TEST_SNAPSHOT_IMAGE", "chann44/zoo-sandbox-desktop:0.1.4-desktop-fix")
    client = sdk.DockerClient(base_url=f"unix://{engine_socket}")
    sandbox_id = f"relay-test-{uuid.uuid4()}"
    volume = docker.volume_name(sandbox_id)
    storage = tmp_path / "objects"
    storage.mkdir()

    def object_path(key):
        return storage / key.replace("/", "_")

    def keys(prefix):
        return [p.name.replace("_", "/") for p in storage.iterdir() if p.name.startswith(prefix.replace("/", "_"))]

    monkeypatch.setattr(remote_snapshots.objects, "put_file", lambda key, path: shutil.copyfile(path, object_path(key)))
    monkeypatch.setattr(remote_snapshots.objects, "get_file", lambda key, path: shutil.copyfile(object_path(key), path))
    monkeypatch.setattr(remote_snapshots.objects, "keys", keys)
    monkeypatch.setattr(
        remote_snapshots.objects, "delete_prefix", lambda prefix: [object_path(k).unlink() for k in keys(prefix)]
    )
    original_helper = docker.helper
    monkeypatch.setattr(docker, "helper", lambda volumes: original_helper(volumes, image=image))
    # Force multiple object parts even for a small archive.
    monkeypatch.setattr(docker, "HOME_PART_BYTES", 128)

    def run(script):
        return client.containers.run(
            image,
            entrypoint=["sh", "-c"],
            command=[script],
            user="root",
            network_mode="none",
            volumes={volume: {"bind": "/home-test", "mode": "rw"}},
            remove=True,
        )

    try:
        client.images.get(image)  # Deliberately never pull images in this test.
        client.volumes.create(volume, labels={"zoo.test": "remote-snapshot"})
        run(
            "mkdir /home-test/.config; printf original > /home-test/.config/profile; "
            "chmod 640 /home-test/.config/profile; chown 1234:2345 /home-test/.config/profile; "
            "ln -s .config/profile /home-test/link"
        )
        with docker.on_host(client, "none", "runc"):
            assert remote_snapshots.snapshot(sandbox_id, "real-snapshot") > 0
            assert len(keys("snapshots/real-snapshot/")) > 1
            run("printf changed > /home-test/.config/profile; touch /home-test/extra")
            remote_snapshots.restore_snapshot(sandbox_id, "real-snapshot")
        result = run(
            "test ! -e /home-test/extra && test -L /home-test/link && "
            "cat /home-test/.config/profile && stat -c ' %u:%g %a' /home-test/.config/profile"
        )
        assert result == b"original 1234:2345 640\n"
    finally:
        try:
            client.volumes.get(volume).remove(force=True)
        except DockerNotFound:
            pass
        client.close()
