import base64
import codecs
import io
import os
import re
import shlex
import tarfile
import time
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Any, NamedTuple

from server.docker import HOME
from server.docker import container as find_container
from server.guest import hub

SANDBOX_USER = "zoo"


# exec_run options the guest can honour; anything else goes through docker
GUEST_EXEC_OPTIONS = {"user", "environment", "workdir", "demux", "stdout", "stderr"}


class ExecResult(NamedTuple):
    """docker's exec_run result: output is bytes, or a (stdout, stderr) pair with demux."""

    exit_code: int
    output: Any


class SandboxContainer:
    """A sandbox container whose exec_run goes through the in-sandbox guest when it is connected, and docker
    otherwise. The docker container is only looked up when something needs it."""

    def __init__(self, container_id: str):
        self.container_id = container_id
        self._container = None

    @property
    def container(self):
        if self._container is None:
            self._container = find_container(self.container_id)
        return self._container

    def exec_run(self, cmd, **kwargs) -> Any:
        kwargs.setdefault("user", SANDBOX_USER)
        guest = hub.for_runtime(self.container_id)
        # the guest runs as the sandbox user, so root commands keep using docker
        if guest is None or not guest.has("exec") or kwargs["user"] != SANDBOX_USER or set(kwargs) - GUEST_EXEC_OPTIONS:
            return self.container.exec_run(cmd, **kwargs)
        argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        demux = kwargs.get("demux", False)
        code, stdout, stderr = guest.exec_run(argv, kwargs.get("environment"), kwargs.get("workdir"), merge=not demux)
        return ExecResult(code, (stdout or None, stderr or None) if demux else stdout)

    def __getattr__(self, name):
        return getattr(self.container, name)


def get_container(container_id: str):
    return SandboxContainer(container_id)


def _fs(container_id: str, args: list[str]) -> str:
    # relative paths resolve against the home directory, as they do on macOS and Windows
    result = get_container(container_id).exec_run(args, workdir=HOME)

    if result.exit_code != 0:
        output = result.output
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        raise RuntimeError(output)

    return result.output.decode(errors="replace")


def run_x(container_id: str, args: list[str], display: str = ":1"):
    container = get_container(container_id)
    result = container.exec_run(
        args,
        environment={"DISPLAY": display},
    )

    if result.exit_code != 0:
        output = result.output
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        raise RuntimeError(output)

    return result


def _guest_mouse(container_id: str, display: str, steps: list[dict]) -> bool:
    """Runs pointer steps through the guest's XTest input; False means the caller falls back to xdotool."""
    guest = hub.for_runtime(container_id)
    if guest is None or not guest.has("input"):
        return False
    guest.call("mouse", {"display": display, "steps": steps})
    return True


def _clicks(button: int, count: int, delay_ms: int) -> list[dict]:
    steps = []
    for i in range(count):
        if i:
            steps.append({"sleep": delay_ms})
        steps += [{"press": button}, {"release": button}]
    return steps


# xdotool's mousemove --sync waits ~16 s when the pointer is already at the target, so the fallback moves without
# it; commands chained in one xdotool run still reach the X server in order.
class MoseTools:
    @staticmethod
    def click(
        container_id: str,
        x: int,
        y: int,
        display: str = ":1",
        button: str = "left",
    ):
        buttons = {"left": 1, "middle": 2, "right": 3}

        if button not in buttons:
            raise ValueError("Invalid button")

        if not _guest_mouse(container_id, display, [{"move": [x, y]}, *_clicks(buttons[button], 1, 0)]):
            run_x(
                container_id,
                [
                    "xdotool",
                    "mousemove",
                    str(x),
                    str(y),
                    "click",
                    str(buttons[button]),
                ],
                display,
            )

        return {
            "success": True,
            "x": x,
            "y": y,
            "button": button,
        }

    @staticmethod
    def move(container_id: str, x: int, y: int, display: str = ":1"):
        if not _guest_mouse(container_id, display, [{"move": [x, y]}]):
            run_x(container_id, ["xdotool", "mousemove", str(x), str(y)], display)
        return {"success": True, "x": x, "y": y}

    @staticmethod
    def double_click(
        container_id: str,
        x: int,
        y: int,
        display: str = ":1",
        button: str = "left",
    ):
        buttons = {"left": 1, "middle": 2, "right": 3}

        if button not in buttons:
            raise ValueError("Invalid button")

        if not _guest_mouse(container_id, display, [{"move": [x, y]}, *_clicks(buttons[button], 2, 100)]):
            run_x(
                container_id,
                [
                    "xdotool",
                    "mousemove",
                    str(x),
                    str(y),
                    "click",
                    "--repeat",
                    "2",
                    "--delay",
                    "100",
                    str(buttons[button]),
                ],
                display,
            )

        return {
            "success": True,
            "x": x,
            "y": y,
            "button": button,
        }

    @staticmethod
    def scroll(
        container_id: str,
        direction: str,
        amount: int = 3,
        x: int | None = None,
        y: int | None = None,
        display: str = ":1",
    ):
        buttons = {"up": 4, "down": 5, "left": 6, "right": 7}

        if direction not in buttons:
            raise ValueError("Invalid scroll direction")

        if amount < 1:
            raise ValueError("Amount must be >= 1")

        if (x is None) != (y is None):
            raise ValueError("Both x and y must be provided")

        steps = [] if x is None else [{"move": [x, y]}]
        if _guest_mouse(container_id, display, steps + _clicks(buttons[direction], amount, 80)):
            return {"success": True, "direction": direction, "amount": amount}

        if x is not None:
            run_x(
                container_id,
                ["xdotool", "mousemove", str(x), str(y)],
                display,
            )

        run_x(
            container_id,
            [
                "xdotool",
                "click",
                "--repeat",
                str(amount),
                "--delay",
                "80",
                str(buttons[direction]),
            ],
            display,
        )

        return {
            "success": True,
            "direction": direction,
            "amount": amount,
        }

    @staticmethod
    def drag(
        container_id: str,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        display: str = ":1",
        button: str = "left",
        duration: float = 0.5,
    ):
        buttons = {"left": 1, "middle": 2, "right": 3}

        if button not in buttons:
            raise ValueError("Invalid button")

        b = buttons[button]
        steps = [
            {"move": [start_x, start_y]},
            {"press": b},
            {"sleep": int(duration * 1000)},
            {"move": [end_x, end_y]},
            {"release": b},
        ]
        if _guest_mouse(container_id, display, [s for s in steps if s.get("sleep", 1)]):
            return {"success": True, "start": [start_x, start_y], "end": [end_x, end_y], "button": button}

        run_x(
            container_id,
            [
                "xdotool",
                "mousemove",
                str(start_x),
                str(start_y),
                "mousedown",
                str(buttons[button]),
                "sleep",
                str(duration),
                "mousemove",
                str(end_x),
                str(end_y),
                "mouseup",
                str(buttons[button]),
            ],
            display,
        )

        return {
            "success": True,
            "start": [start_x, start_y],
            "end": [end_x, end_y],
            "button": button,
        }


def _guest_keyboard(container_id: str, display: str, args: dict, timeout: float = 60) -> bool:
    """Types or presses keys through the guest's XTest input; False means the caller falls back to xdotool."""
    guest = hub.for_runtime(container_id)
    if guest is None or not guest.has("input"):
        return False
    guest.call("keyboard", {"display": display, **args}, timeout=timeout)
    return True


class KeyboardTools:
    @staticmethod
    def type_text(
        container_id: str,
        text: str,
        display: str = ":1",
        delay: int = 12,
    ):
        if text and _guest_keyboard(
            container_id, display, {"text": text, "delay": delay}, 60 + len(text) * max(delay, 0) / 1000
        ):
            return {"success": True, "length": len(text)}

        run_x(
            container_id,
            [
                "xdotool",
                "type",
                "--clearmodifiers",
                "--delay",
                str(delay),
                "--",
                text,
            ],
            display,
        )

        return {
            "success": True,
            "length": len(text),
        }

    @staticmethod
    def press_key(
        container_id: str,
        key: str,
        display: str = ":1",
    ):
        if not _guest_keyboard(container_id, display, {"keys": key}):
            run_x(
                container_id,
                ["xdotool", "key", "--clearmodifiers", key],
                display,
            )

        return {
            "success": True,
            "key": key,
        }

    @staticmethod
    def hotkey(
        container_id: str,
        *keys: str,
        display: str = ":1",
    ):
        if not keys:
            raise ValueError("At least one key is required")

        if _guest_keyboard(container_id, display, {"keys": "+".join(keys)}):
            return {"success": True, "keys": list(keys)}

        run_x(
            container_id,
            [
                "xdotool",
                "key",
                "--clearmodifiers",
                "+".join(keys),
            ],
            display,
        )

        return {
            "success": True,
            "keys": list(keys),
        }


class WindowTools:
    @staticmethod
    def windows_list(container_id: str, display: str = ":1"):
        result = run_x(
            container_id,
            ["xdotool", "search", "--onlyvisible", "--class", ".*"],
            display,
        )

        windows = []

        for window_id in result.output.decode().splitlines():
            title_result = run_x(
                container_id,
                ["xdotool", "getwindowname", window_id],
                display,
            )

            title = title_result.output.decode(errors="replace").strip()

            windows.append(
                {
                    "id": window_id,
                    "title": title,
                }
            )

        return windows

    @staticmethod
    def window_focus(
        container_id: str,
        window_id: str,
        display: str = ":1",
    ):
        run_x(
            container_id,
            ["xdotool", "windowactivate", "--sync", str(window_id)],
            display,
        )

        return True

    @staticmethod
    def window_minimize(
        container_id: str,
        window_id: str,
        display: str = ":1",
    ):
        run_x(
            container_id,
            ["xdotool", "windowminimize", str(window_id)],
            display,
        )

        return True

    @staticmethod
    def window_restore(
        container_id: str,
        window_id: str,
        display: str = ":1",
    ):
        run_x(
            container_id,
            ["xdotool", "windowmap", str(window_id)],
            display,
        )

        run_x(
            container_id,
            ["xdotool", "windowactivate", str(window_id)],
            display,
        )

        return True

    @staticmethod
    def window_maximize(
        container_id: str,
        window_id: str,
        display: str = ":1",
    ):
        run_x(
            container_id,
            [
                "wmctrl",
                "-ir",
                str(window_id),
                "-b",
                "add,maximized_vert,maximized_horz",
            ],
            display,
        )

        return True

    @staticmethod
    def window_unmaximize(
        container_id: str,
        window_id: str,
        display: str = ":1",
    ):
        run_x(
            container_id,
            [
                "wmctrl",
                "-ir",
                str(window_id),
                "-b",
                "remove,maximized_vert,maximized_horz",
            ],
            display,
        )

        return True

    @staticmethod
    def window_close(
        container_id: str,
        window_id: str,
        display: str = ":1",
    ):
        run_x(
            container_id,
            ["xdotool", "windowclose", str(window_id)],
            display,
        )

        return True


class AppTools:
    @staticmethod
    def installed_apps(container_id: str) -> dict:
        container = get_container(container_id)

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

        gui_res = container.exec_run(["bash", "-c", gui_script])
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

        apt_res = container.exec_run(["apt-mark", "showmanual"])
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
        container = get_container(container_id)
        env = {"DISPLAY": display}

        cleaned_cmd = re.sub(
            r"\s%[uUfFiIcCkK]",
            "",
            command,
        ).strip()

        binary_name = os.path.basename(cleaned_cmd.split()[0]) if cleaned_cmd else ""

        launch_cmd = f"nohup {cleaned_cmd} >/dev/null 2>&1 & echo $!"

        res = container.exec_run(
            ["bash", "-c", launch_cmd],
            environment=env,
        )

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
            check = container.exec_run(
                ["bash", "-c", f"kill -0 {pid} 2>/dev/null && echo alive"],
                environment=env,
            )

            if b"alive" in check.output:
                pid_alive = True
                break

            time.sleep(0.2)

        win_deadline = time.time() + timeout
        window_found = False

        while time.time() < win_deadline:
            win_check = container.exec_run(
                ["xdotool", "search", "--pid", str(pid)],
                environment=env,
            )

            if win_check.output.decode(errors="replace").strip():
                window_found = True
                break

            name_check = container.exec_run(
                ["xdotool", "search", "--onlyvisible", "--name", binary_name],
                environment=env,
            )

            if name_check.output.decode(errors="replace").strip():
                window_found = True
                break

            time.sleep(0.3)

        return {
            "started": pid_alive or window_found,
            "pid": pid,
            "pid_alive": pid_alive,
            "window_found": window_found,
        }

    @staticmethod
    def close_app(
        container_id: str,
        target: str,
        display: str = ":1",
    ) -> bool:
        container = get_container(container_id)
        env = {"DISPLAY": display}

        if target.isdigit():
            res = container.exec_run(
                ["xdotool", "windowclose", target],
                environment=env,
            )
            return res.exit_code == 0

        search_res = container.exec_run(
            ["xdotool", "search", "--onlyvisible", "--name", target],
            environment=env,
        )

        window_ids = search_res.output.decode(errors="replace").splitlines()

        if not window_ids:
            return False

        success = True

        for window_id in window_ids:
            window_id = window_id.strip()

            if window_id:
                close_res = container.exec_run(
                    ["xdotool", "windowclose", window_id],
                    environment=env,
                )

                if close_res.exit_code != 0:
                    success = False

        return success


class ShellTools:
    @staticmethod
    def execute_command(
        container_id: str,
        command: str,
        timeout: int = 30,
    ) -> dict:
        result = get_container(container_id).exec_run(
            ["timeout", "-k", "2", str(timeout), "sh", "-lc", command],
            demux=True,
        )
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
        container = get_container(container_id)
        name = f"/tmp/zoo-exec/{os.urandom(8).hex()}"
        script = base64.b64encode(command.encode()).decode()
        setup = (
            f"mkdir -p /tmp/zoo-exec && echo {script} | base64 -d > {name}.sh && : > {name}.out && : > {name}.err && "
            f"(nohup sh -c 'timeout -k 2 {int(timeout)} sh -l {name}.sh > {name}.out 2> {name}.err; "
            f"echo $? > {name}.code' > /dev/null 2>&1 &)"
        )
        result = container.exec_run(["sh", "-c", setup], demux=True)
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
                result = container.exec_run(["sh", "-c", poll], demux=True)
                stdout, stderr = result.output
                for stream, data in (("stdout", stdout or b""), ("stderr", stderr or b"")):
                    if data:
                        offsets[stream] += len(data)
                        text = decoders[stream].decode(data)
                        if text:
                            yield stream, text
                if result.exit_code == POLL_DONE:
                    code = container.exec_run(["cat", f"{name}.code"]).output
                    yield "exit", int((code or b"-1").strip() or -1)
                    return
                if result.exit_code != 0:
                    raise RuntimeError("lost track of the command")
                time.sleep(POLL)
        finally:
            container.exec_run(["sh", "-c", f"rm -f {name}.sh {name}.out {name}.err {name}.code"])


POLL = 0.3
POLL_DONE = 100


class FileSystem:
    @staticmethod
    def upload_file(
        container_id: str,
        local_path: str,
        dest_dir: str,
    ):
        if not os.path.isfile(local_path):
            raise FileNotFoundError(local_path)

        filename = os.path.basename(local_path)
        buffer = io.BytesIO()

        with tarfile.open(fileobj=buffer, mode="w") as tar:
            tar.add(local_path, arcname=filename)

        container = get_container(container_id)

        success = container.put_archive(
            dest_dir,
            buffer.getvalue(),
        )

        if not success:
            raise RuntimeError("Docker failed to upload file")

        return {
            "success": True,
            "filename": filename,
            "destination": str(PurePosixPath(dest_dir) / filename),
        }

    @staticmethod
    def download_file(
        container_id: str,
        container_path: str,
    ) -> bytes:
        container = get_container(container_id)

        stream, _ = container.get_archive(container_path)
        archive_bytes = b"".join(stream)

        with tarfile.open(
            fileobj=io.BytesIO(archive_bytes),
            mode="r:*",
        ) as tar:
            members = [member for member in tar.getmembers() if member.isfile()]

            if not members:
                raise FileNotFoundError(container_path)

            extracted = tar.extractfile(members[0])

            if extracted is None:
                raise RuntimeError("Could not read archived file")

            return extracted.read()

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
        return _fs(container_id, ["cat", "--", path])

    @staticmethod
    def write_file(container_id: str, path: str, content: str):
        return FileSystem.write_bytes(container_id, path, content.encode("utf-8"))

    @staticmethod
    def write_bytes(container_id: str, path: str, data: bytes) -> dict:
        target = PurePosixPath(HOME) / path

        if target.name in ("", ".", ".."):
            raise ValueError("A filename is required")

        guest = hub.for_runtime(container_id)
        if guest is not None and guest.has("files"):
            guest.write(str(target), data)
            return {"success": True, "path": str(target), "size": len(data)}

        buffer = io.BytesIO()

        with tarfile.open(fileobj=buffer, mode="w") as tar:
            info = tarfile.TarInfo(name=target.name)
            info.size = len(data)
            info.mode = 0o644

            tar.addfile(info, io.BytesIO(data))

        container = get_container(container_id)
        container.exec_run(["mkdir", "-p", str(target.parent)])

        success = container.put_archive(
            str(target.parent),
            buffer.getvalue(),
        )

        if not success:
            raise RuntimeError("Failed to write file")

        return {
            "success": True,
            "path": str(target),
            "size": len(data),
        }

    @staticmethod
    def read_bytes(container_id: str, path: str) -> bytes:
        """A file's bytes; relative paths are in the home directory."""
        target = PurePosixPath(HOME) / path
        guest = hub.for_runtime(container_id)
        if guest is not None and guest.has("files"):
            return guest.read(str(target))
        result = get_container(container_id).exec_run(["cat", "--", str(target)], demux=True)
        stdout, stderr = result.output
        if result.exit_code != 0:
            raise FileNotFoundError((stderr or b"").decode(errors="replace").strip() or str(target))
        return stdout or b""


class ObserveTools:
    @staticmethod
    def screenshot(container_id: str, display: str = ":1"):
        container = get_container(container_id)

        result = container.exec_run(
            ["import", "-display", display, "-window", "root", "png:-"],
            stdout=True,
            stderr=True,
        )

        if result.exit_code != 0:
            raise RuntimeError(result.output.decode(errors="replace"))

        return result.output
