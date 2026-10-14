import base64
import codecs
import os
import re
import time
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Any, NamedTuple

from server.errors import ZooError
from server.guest import hub
from server.runtime import HOME


class ExecResult(NamedTuple):
    """Command output is bytes, or a (stdout, stderr) pair with demux."""

    exit_code: int
    output: Any


def _require_guest(container_id: str, service: str):
    guest = hub.for_runtime(container_id)
    if guest is None or not guest.has(service):
        raise ZooError("guest_unavailable", "the sandbox's guest agent is not connected")
    return guest


def run(
    container_id: str,
    argv: list[str],
    environment: dict[str, str] | None = None,
    workdir: str | None = None,
    demux: bool = False,
) -> ExecResult:
    """Runs argv as the sandbox user through the sandbox guest."""
    code, stdout, stderr = _require_guest(container_id, "exec").run(argv, environment, workdir, merge=not demux)
    return ExecResult(code, (stdout or None, stderr or None) if demux else stdout + stderr)


def _fs(container_id: str, args: list[str]) -> str:
    # relative paths resolve against the home directory, as they do on macOS and Windows
    result = run(container_id, args, workdir=HOME)

    if result.exit_code != 0:
        output = result.output
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        raise RuntimeError(output)

    return result.output.decode(errors="replace")


def _window(container_id: str, action: str, window_id: str = "", display: str = ":1") -> dict:
    return _require_guest(container_id, "windows").call(
        "window", {"action": action, "id": str(window_id), "display": display}, timeout=30
    )[0]


class WindowTools:
    @staticmethod
    def windows_list(container_id: str, display: str = ":1"):
        return _window(container_id, "list", display=display).get("windows") or []

    @staticmethod
    def window_focus(container_id: str, window_id: str, display: str = ":1"):
        _window(container_id, "focus", window_id, display)
        return True

    @staticmethod
    def window_minimize(container_id: str, window_id: str, display: str = ":1"):
        _window(container_id, "minimize", window_id, display)
        return True

    @staticmethod
    def window_restore(container_id: str, window_id: str, display: str = ":1"):
        _window(container_id, "restore", window_id, display)
        return True

    @staticmethod
    def window_maximize(container_id: str, window_id: str, display: str = ":1"):
        _window(container_id, "maximize", window_id, display)
        return True

    @staticmethod
    def window_unmaximize(container_id: str, window_id: str, display: str = ":1"):
        _window(container_id, "unmaximize", window_id, display)
        return True

    @staticmethod
    def window_close(container_id: str, window_id: str, display: str = ":1"):
        _window(container_id, "close", window_id, display)
        return True


class AppTools:
    @staticmethod
    def installed_apps(container_id: str) -> dict:
        gui_script = """
        for f in /usr/share/applications/*.desktop; do
            [ -f "$f" ] || continue
            name=$(grep -m1 '^Name=' "$f" | cut -d'=' -f2-)
            exec=$(grep -m1 '^Exec=' "$f" | cut -d'=' -f2-)
            no_display=$(grep -m1 '^NoDisplay=' "$f" | cut -d'=' -f2-)

            if [ "$no_display" != "true" ] && [ -n "$name" ]; then
                echo "$name | $exec"
            fi
        done
        """

        gui_res = run(container_id, ["bash", "-c", gui_script])
        gui_output = gui_res.output.decode(errors="replace").strip()
        if gui_res.exit_code != 0:
            raise RuntimeError(gui_output)

        gui_apps = []

        if gui_output:
            for line in gui_output.splitlines():
                if "|" not in line:
                    continue

                name, raw_exec = line.split("|", 1)
                raw_exec = raw_exec.strip()

                cleaned_exec = re.sub(
                    r"\s%[uUfFiIcCkK]",
                    "",
                    raw_exec,
                ).strip()

                first_token = cleaned_exec.split()[0] if cleaned_exec else ""
                binary_name = os.path.basename(first_token)

                gui_apps.append(
                    {
                        "name": name.strip(),
                        "binary": binary_name,
                        "exec": cleaned_exec,
                    }
                )

        apt_res = run(container_id, ["apt-mark", "showmanual"])
        apt_output = apt_res.output.decode(errors="replace").strip()

        apt_packages = [pkg.strip() for pkg in apt_output.splitlines() if pkg.strip()]

        return {
            "gui_apps": gui_apps,
            "apt_packages": apt_packages,
        }

    @staticmethod
    def open_app(
        container_id: str,
        command: str,
        display: str = ":1",
        timeout: float = 4.0,
    ) -> dict:
        env = {"DISPLAY": display}

        cleaned_cmd = re.sub(
            r"\s%[uUfFiIcCkK]",
            "",
            command,
        ).strip()

        launch_cmd = f"nohup {cleaned_cmd} >/dev/null 2>&1 & echo $!"

        res = run(container_id, ["bash", "-c", launch_cmd], env)

        output = res.output.decode(errors="replace").strip()

        try:
            pid = int(output.splitlines()[-1])
        except (ValueError, IndexError):
            return {
                "started": False,
                "pid": None,
                "reason": "failed to capture pid",
            }

        deadline = time.time() + timeout
        pid_alive = False

        while time.time() < deadline:
            check = run(container_id, ["bash", "-c", f"kill -0 {pid} 2>/dev/null && echo alive"], env)

            if b"alive" in check.output:
                pid_alive = True
                break

            time.sleep(0.2)

        return {
            "started": pid_alive,
            "pid": pid,
            "pid_alive": pid_alive,
            "window_found": False,
        }

    @staticmethod
    def close_app(container_id: str, target: str, display: str = ":1") -> bool:
        windows = WindowTools.windows_list(container_id, display)
        matching = [
            window
            for window in windows
            if str(window["id"]) == target or target in (window.get("title"), window.get("app"))
        ]
        for window in matching:
            WindowTools.window_close(container_id, str(window["id"]), display)
        return bool(matching)


class ShellTools:
    @staticmethod
    def execute_command(
        container_id: str,
        command: str,
        timeout: int = 30,
    ) -> dict:
        result = run(container_id, ["timeout", "-k", "2", str(timeout), "sh", "-lc", command], demux=True)
        stdout, stderr = result.output
        return {
            "exit_code": result.exit_code,
            "stdout": (stdout or b"").decode(errors="replace"),
            "stderr": (stderr or b"").decode(errors="replace"),
            "timed_out": result.exit_code == 124,
        }

    @staticmethod
    def stream_command(container_id: str, command: str, timeout: int = 30) -> Iterator[tuple[str, Any]]:
        """Runs a command and yields ("stdout" | "stderr", text) as its output arrives, then ("exit", exit code).

        The command runs detached in the sandbox, writing to files under /tmp/zoo-exec, which are read every
        POLL seconds from where the last read stopped; so it works wherever exec does, and a slow reader can't stall
        the command."""
        name = f"/tmp/zoo-exec/{os.urandom(8).hex()}"
        script = base64.b64encode(command.encode()).decode()
        setup = (
            f"mkdir -p /tmp/zoo-exec && echo {script} | base64 -d > {name}.sh && : > {name}.out && : > {name}.err && "
            f"(nohup sh -c 'timeout -k 2 {int(timeout)} sh -l {name}.sh > {name}.out 2> {name}.err; "
            f"echo $? > {name}.code' > /dev/null 2>&1 &)"
        )
        result = run(container_id, ["sh", "-c", setup], demux=True)
        if result.exit_code != 0:
            _, err = result.output
            raise RuntimeError(f"couldn't start the command: {(err or b'').decode(errors='replace')}")
        decoders = {
            "stdout": codecs.getincrementaldecoder("utf-8")("replace"),
            "stderr": codecs.getincrementaldecoder("utf-8")("replace"),
        }
        offsets = {"stdout": 0, "stderr": 0}
        try:
            while True:
                # the exit code is checked before reading, so a finished command's output is all read this round
                poll = (
                    f"[ -f {name}.code ] && done=1; tail -c +{offsets['stdout'] + 1} {name}.out; "
                    f'tail -c +{offsets["stderr"] + 1} {name}.err >&2; [ -n "$done" ] && exit {POLL_DONE}; exit 0'
                )
                result = run(container_id, ["sh", "-c", poll], demux=True)
                stdout, stderr = result.output
                for stream, data in (("stdout", stdout or b""), ("stderr", stderr or b"")):
                    if data:
                        offsets[stream] += len(data)
                        text = decoders[stream].decode(data)
                        if text:
                            yield stream, text
                if result.exit_code == POLL_DONE:
                    code = run(container_id, ["cat", f"{name}.code"]).output
                    yield "exit", int((code or b"-1").strip() or -1)
                    return
                if result.exit_code != 0:
                    raise RuntimeError("lost track of the command")
                time.sleep(POLL)
        finally:
            run(container_id, ["sh", "-c", f"rm -f {name}.sh {name}.out {name}.err {name}.code"])


POLL = 0.3
POLL_DONE = 100


class FileSystem:
    @staticmethod
    def upload_file(container_id: str, local_path: str, dest_dir: str):
        guest = _require_guest(container_id, "files")
        if not os.path.isfile(local_path):
            raise FileNotFoundError(local_path)
        filename = os.path.basename(local_path)
        target = PurePosixPath(HOME) / dest_dir / filename
        with open(local_path, "rb") as source:
            guest.write(str(target), source.read())
        return {"success": True, "filename": filename, "destination": str(target)}

    @staticmethod
    def download_file(container_id: str, container_path: str) -> bytes:
        return FileSystem.read_bytes(container_id, container_path)

    @staticmethod
    def list_files(container_id: str, path: str = "."):
        output = _fs(
            container_id,
            [
                "find",
                path,
                "-maxdepth",
                "1",
                "-mindepth",
                "1",
                "-printf",
                "%f\\t%y\\t%s\\n",
            ],
        )

        files = []

        for line in output.splitlines():
            parts = line.split("\t")

            if len(parts) == 3:
                name, kind, size = parts

                files.append(
                    {
                        "name": name,
                        "type": "directory" if kind == "d" else "file",
                        "size": int(size),
                    }
                )

        return files

    @staticmethod
    def get_file_info(container_id: str, path: str):
        output = _fs(
            container_id,
            ["stat", "-c", "%n|%F|%s|%a|%Y", path],
        ).strip()

        name, kind, size, mode, mtime = output.split("|", 4)

        return {
            "path": name,
            "type": kind,
            "size": int(size),
            "permissions": mode,
            "modified_at": int(mtime),
        }

    @staticmethod
    def create_directory(container_id: str, path: str):
        _fs(container_id, ["mkdir", "-p", path])

        return {
            "success": True,
            "path": path,
        }

    @staticmethod
    def delete_file(container_id: str, path: str):
        _fs(container_id, ["rm", "-rf", "--", path])

        return {
            "success": True,
            "path": path,
        }

    @staticmethod
    def move_file(container_id: str, source: str, destination: str):
        _fs(container_id, ["mv", "--", source, destination])

        return {
            "success": True,
            "source": source,
            "destination": destination,
        }

    @staticmethod
    def copy_file(container_id: str, source: str, destination: str):
        _fs(container_id, ["cp", "-a", "--", source, destination])

        return {
            "success": True,
            "source": source,
            "destination": destination,
        }

    @staticmethod
    def read_file(container_id: str, path: str):
        return FileSystem.read_bytes(container_id, path).decode(errors="replace")

    @staticmethod
    def write_file(container_id: str, path: str, content: str):
        return FileSystem.write_bytes(container_id, path, content.encode("utf-8"))

    @staticmethod
    def write_bytes(container_id: str, path: str, data: bytes) -> dict:
        target = PurePosixPath(HOME) / path

        if target.name in ("", ".", ".."):
            raise ValueError("A filename is required")

        _require_guest(container_id, "files").write(str(target), data)
        return {"success": True, "path": str(target), "size": len(data)}

    @staticmethod
    def read_bytes(container_id: str, path: str) -> bytes:
        """A file's bytes; relative paths are in the home directory."""
        target = PurePosixPath(HOME) / path
        return _require_guest(container_id, "files").read(str(target))
