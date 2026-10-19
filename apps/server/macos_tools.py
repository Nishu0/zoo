import shlex
import time

from apps.server.macos import ENV_FILE, connected, guest, guest_check

APP_DIRS = '/Applications /System/Applications /System/Applications/Utilities "$HOME/Applications"'


def window_ref(window_id: str) -> tuple[str, int]:
    app, _, index = str(window_id).rpartition(":")
    if not app or not index.isdigit():
        raise ValueError("window_id must look like 'App:1' (from windows_list)")
    return app, int(index)


def window(container_id: str, action: str, window_id: str = "") -> dict:
    """The guest's window service (AX): "list", or an action on one window."""
    agent = connected(container_id, "windows")
    if window_id:
        window_ref(window_id)
    result, _ = agent.call("window", {"action": action, "id": window_id}, timeout=30)
    return result


def on_window(container_id: str, window_id: str, action: str):
    window(container_id, action, window_id)
    return True


class MacWindows:
    @staticmethod
    def windows_list(container_id: str, display: str = ":1"):
        listed = window(container_id, "list")
        return [{"id": w["id"], "title": w["title"], "app": w["app"]} for w in listed.get("windows") or []]

    @staticmethod
    def window_focus(container_id: str, window_id: str, display: str = ":1"):
        return on_window(container_id, window_id, "focus")

    @staticmethod
    def window_minimize(container_id: str, window_id: str, display: str = ":1"):
        return on_window(container_id, window_id, "minimize")

    @staticmethod
    def window_restore(container_id: str, window_id: str, display: str = ":1"):
        return on_window(container_id, window_id, "restore")

    @staticmethod
    def window_maximize(container_id: str, window_id: str, display: str = ":1"):
        return on_window(container_id, window_id, "maximize")

    @staticmethod
    def window_unmaximize(container_id: str, window_id: str, display: str = ":1"):
        return on_window(container_id, window_id, "unmaximize")

    @staticmethod
    def window_close(container_id: str, window_id: str, display: str = ":1"):
        return on_window(container_id, window_id, "close")


def app_path(container_id: str, name: str) -> str | None:
    bundle = name if name.endswith(".app") else f"{name}.app"
    if bundle.startswith("/"):
        return bundle if guest(container_id, f"[ -d {shlex.quote(bundle)} ]")[0] == 0 else None
    code, out, _ = guest(
        container_id,
        f'set -- {shlex.quote(bundle)}; for d in {APP_DIRS}; do [ -d "$d/$1" ] && echo "$d/$1" && exit 0; done; exit 1',
    )
    return out.decode(errors="replace").strip() if code == 0 else None


def app_running(container_id: str, path: str) -> bool:
    return guest(container_id, f"pgrep -f {shlex.quote(f'{path}/Contents/MacOS/')} >/dev/null")[0] == 0


class MacApps:
    @staticmethod
    def installed_apps(container_id: str) -> dict:
        output = guest_check(container_id, f'for d in {APP_DIRS}; do ls -d "$d"/*.app 2>/dev/null; done; true')
        apps = []
        for path in output.splitlines():
            name = path.rsplit("/", 1)[-1][:-4]
            apps.append({"name": name, "binary": name, "exec": f"open -a {shlex.quote(path)}"})
        code, brew, _ = guest(container_id, "command -v brew >/dev/null && brew leaves")
        packages = brew.decode(errors="replace").split() if code == 0 else []
        return {"gui_apps": apps, "brew_packages": packages}

    @staticmethod
    def open_app(container_id: str, command: str, display: str = ":1", timeout: float = 4.0) -> dict:
        command = command.strip()
        if command.startswith("open "):
            guest_check(container_id, command)
            return {"started": True, "pid": None, "pid_alive": True, "window_found": False}
        path = app_path(container_id, command)
        if path is None:
            output = guest_check(container_id, f"nohup {command} >/dev/null 2>&1 & echo $!").strip()
            pid = int(output.splitlines()[-1]) if output else None
            alive = pid is not None and guest(container_id, f"kill -0 {pid}")[0] == 0
            return {"started": alive, "pid": pid, "pid_alive": alive, "window_found": False}
        guest_check(container_id, f"open -a {shlex.quote(path)}")
        deadline = time.time() + timeout
        running = False
        while time.time() < deadline and not running:
            running = app_running(container_id, path)
            if not running:
                time.sleep(0.3)
        return {"started": running, "pid": None, "pid_alive": running, "window_found": running}

    @staticmethod
    def close_app(container_id: str, target: str, display: str = ":1") -> bool:
        if ":" in target and target.rpartition(":")[2].isdigit():
            try:
                return MacWindows.window_close(container_id, target)
            except RuntimeError:
                return False
        code, _, _ = guest(container_id, f"killall {shlex.quote(target)}")
        return code == 0


def open_url(container_id: str, url: str, display: str = ":1") -> dict:
    guest_check(container_id, f"open {shlex.quote(url)}")
    return {"opened": url}


class MacShell:
    @staticmethod
    def execute_command(container_id: str, command: str, timeout: int = 30) -> dict:
        script = (
            f"[ -f {ENV_FILE} ] && . {ENV_FILE}; "
            f"perl -e 'alarm shift; exec @ARGV' {int(timeout)} zsh -lc {shlex.quote(command)}"
        )
        code, out, err = guest(container_id, script, timeout=timeout + 15)
        return {
            "exit_code": code,
            "stdout": out.decode(errors="replace"),
            "stderr": err.decode(errors="replace"),
            "timed_out": code == 142,
        }


def fetch_url(container_id: str, url: str, timeout: int = 20) -> dict:
    return MacShell.execute_command(
        container_id, f"curl -sSL --max-time {int(timeout)} {shlex.quote(url)} | head -c 200000", timeout + 5
    )


class MacFiles:
    @staticmethod
    def list_files(container_id: str, path: str = "."):
        output = guest_check(
            container_id,
            f"find {shlex.quote(path)} -mindepth 1 -maxdepth 1 -exec stat -f '%N%t%HT%t%z' {{}} +",
        )
        files = []
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                name, kind, size = parts
                files.append(
                    {
                        "name": name.rsplit("/", 1)[-1],
                        "type": "directory" if kind == "Directory" else "file",
                        "size": int(size),
                    }
                )
        return files

    @staticmethod
    def get_file_info(container_id: str, path: str):
        output = guest_check(container_id, f"stat -f '%N|%HT|%z|%Lp|%m' {shlex.quote(path)}").strip()
        name, kind, size, mode, mtime = output.split("|", 4)
        return {"path": name, "type": kind.lower(), "size": int(size), "permissions": mode, "modified_at": int(mtime)}

    @staticmethod
    def create_directory(container_id: str, path: str):
        guest_check(container_id, f"mkdir -p {shlex.quote(path)}")
        return {"success": True, "path": path}

    @staticmethod
    def delete_file(container_id: str, path: str):
        guest_check(container_id, f"rm -rf -- {shlex.quote(path)}")
        return {"success": True, "path": path}

    @staticmethod
    def move_file(container_id: str, source: str, destination: str):
        guest_check(container_id, f"mv -- {shlex.quote(source)} {shlex.quote(destination)}")
        return {"success": True, "source": source, "destination": destination}

    @staticmethod
    def copy_file(container_id: str, source: str, destination: str):
        guest_check(container_id, f"cp -a -- {shlex.quote(source)} {shlex.quote(destination)}")
        return {"success": True, "source": source, "destination": destination}

    @staticmethod
    def read_file(container_id: str, path: str):
        return guest_check(container_id, f"cat -- {shlex.quote(path)}")

    @staticmethod
    def write_file(container_id: str, path: str, content: str):
        name = path.rstrip("/").rsplit("/", 1)[-1]
        if name in ("", ".", ".."):
            raise ValueError("A filename is required")
        data = content.encode("utf-8")
        guest_check(container_id, f"cat > {shlex.quote(path)}", stdin=data)
        return {"success": True, "path": path, "size": len(data)}
