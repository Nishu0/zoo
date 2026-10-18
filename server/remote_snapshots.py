"""Relay remote Docker home snapshots through the control plane's object-store access.

Docker transport carries archive bytes; remote hosts need no route to the object store.
Temporary files bound memory while preserving the portable numbered gzip format.
Callers scope the Docker backend to the selected host before invoking these functions.
"""

import gzip
import shutil
import tarfile
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from server import docker, objects
from server.errors import ZooError

CHUNK_BYTES = 1 << 20


def snapshot(sandbox_id: str, snapshot_id: str) -> int:
    reader = docker.helper({docker.volume_name(sandbox_id): "/from"})
    prefix = docker.snapshot_prefix(snapshot_id)
    try:
        reader.start()
        size = int(docker.helper_check(reader, "du -sb /from | cut -f1").strip() or 0)
        objects.delete_prefix(prefix)
        api = docker.docker_client.api
        execution = api.exec_create(reader.id, ["tar", "-czf", "-", "-C", "/from", "."], user="root")
        stream = api.exec_start(execution["Id"], stream=True, demux=True)
        error = bytearray()
        try:
            with tempfile.TemporaryDirectory(prefix="zoo-remote-snapshot-") as directory:
                part = Path(directory) / "part"
                index, written = 0, 0
                with part.open("wb") as output:
                    for stdout, stderr in stream:
                        if stderr:
                            error.extend(stderr)
                            del error[:-2000]
                        data = memoryview(stdout or b"")
                        while data:
                            count = min(len(data), docker.HOME_PART_BYTES - written)
                            output.write(data[:count])
                            written += count
                            data = data[count:]
                            if written == docker.HOME_PART_BYTES:
                                output.flush()
                                objects.put_file(f"{prefix}home.{index:04d}", str(part))
                                output.seek(0)
                                output.truncate()
                                index, written = index + 1, 0
                    if written:
                        output.flush()
                        objects.put_file(f"{prefix}home.{index:04d}", str(part))
                result = api.exec_inspect(execution["Id"])
                if result.get("ExitCode") != 0:
                    raise RuntimeError(error.decode(errors="replace") or "remote snapshot archive failed")
        finally:
            close = getattr(stream, "close", None)
            if close:
                # Docker's stream cancellation can fail after EOF, and SSH streams do
                # not support its socket shutdown operation. Helper removal ends exec.
                with suppress(OSError, docker.docker.errors.DockerException):
                    close()
        return size
    except Exception:
        objects.delete_prefix(prefix)
        raise
    finally:
        reader.remove(force=True)


def restore_snapshot(sandbox_id: str, snapshot_id: str):
    prefix = docker.snapshot_prefix(snapshot_id)
    keys = sorted(objects.keys(prefix))
    if not keys:
        raise ZooError("gone", "The snapshot is gone from object storage", "Create a new snapshot before restoring.")
    if keys != [f"{prefix}home.{part:04d}" for part in range(len(keys))]:
        raise ZooError("upstream_error", "The snapshot's numbered parts are incomplete")
    # Finish downloading and validating gzip before replacing the existing home.
    with tempfile.TemporaryDirectory(prefix="zoo-remote-restore-") as directory:
        compressed = Path(directory) / "home.tar.gz"
        part = Path(directory) / "part"
        archive = Path(directory) / "home.tar"
        with compressed.open("wb") as output:
            for key in keys:
                objects.get_file(key, str(part))
                with part.open("rb") as source:
                    shutil.copyfileobj(source, output, CHUNK_BYTES)
        with gzip.open(compressed, "rb") as source, archive.open("wb") as output:
            shutil.copyfileobj(source, output, CHUNK_BYTES)
        with tarfile.open(archive, "r:") as contents:
            while contents.next() is not None:
                cast(Any, contents).members.clear()  # Keep validation memory bounded for many files.
        writer = docker.helper({docker.volume_name(sandbox_id): "/to"})
        try:
            writer.start()
            docker.helper_check(writer, "find /to -mindepth 1 -delete")
            with archive.open("rb") as source:
                if not writer.put_archive("/to", source):
                    raise ZooError("upstream_error", "Could not restore the remote sandbox's home archive")
        finally:
            writer.remove(force=True)
