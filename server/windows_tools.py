"""Tools for Windows sandboxes. Screen, mouse and keyboard go through the gateway (server/registry.py); everything else goes
through zoo-guest in the desktop session: window tools through its windows service, and app, shell and file tools as
PowerShell."""

import re
import time

from server.windows import (
    ENV_FILE,
    HOME,
    desktop,
    desktop_json,
    guest,
    guest_check,
    guest_json,
    q,
    window,
)


def path_of(path: str) -> str:
    if path == "~" or path.startswith(("~/", "~\\")):
        path = HOME + path[1:]
    return q(path)


def on_window(container_id: str, window_id: str, action: str, cmd: int = 0):
    window(container_id, action, window_id, cmd)
    return True


def list_windows(container_id: str) -> list[dict]:
    return window(container_id, "list") or []


class WinWindows:
    @staticmethod
    def windows_list(container_id: str, display: str = ":1"):
        return [{"id": w["id"], "title": w["title"], "app": w["app"]} for w in list_windows(container_id)]

    @staticmethod
    def window_focus(container_id: str, window_id: str, display: str = ":1"):
        return on_window(container_id, window_id, "focus")

    @staticmethod
    def window_minimize(container_id: str, window_id: str, display: str = ":1"):
        return on_window(container_id, window_id, "show", 6)

    @staticmethod
    def window_restore(container_id: str, window_id: str, display: str = ":1"):
        return on_window(container_id, window_id, "focus")

    @staticmethod
    def window_maximize(container_id: str, window_id: str, display: str = ":1"):
        return on_window(container_id, window_id, "show", 3)

    @staticmethod
    def window_unmaximize(container_id: str, window_id: str, display: str = ":1"):
        return on_window(container_id, window_id, "show", 9)

    @staticmethod
    def window_close(container_id: str, window_id: str, display: str = ":1"):
        return on_window(container_id, window_id, "close")


INSTALLED_APPS = r"""
$shell = New-Object -ComObject WScript.Shell
$targets = @{}
foreach ($dir in "$env:ProgramData\Microsoft\Windows\Start Menu\Programs", "$env:APPDATA\Microsoft\Windows\Start Menu\Programs") {
    Get-ChildItem -Path $dir -Recurse -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {
        $target = $shell.CreateShortcut($_.FullName).TargetPath
        if ($target -like '*.exe') { $targets[$_.BaseName] = $target }
    }
}
$apps = @(Get-StartApps | ForEach-Object {
    $exe = $targets[$_.Name]
    if (-not $exe -and $_.AppID -like '*.exe') { $exe = $_.AppID }
    [ordered]@{
        name = $_.Name
        binary = $(if ($exe) { Split-Path $exe -Leaf } else { $_.AppID })
        exec = $(if ($exe) { $exe } else { "shell:AppsFolder\$($_.AppID)" })
    }
})
ConvertTo-Json -Compress -Depth 3 -InputObject @{ gui_apps = $apps }
"""

START_APP = r"""
$command = __COMMAND__.Trim()
$app = Get-StartApps | Where-Object { $_.Name -eq $command -or $_.AppID -eq $command } | Select-Object -First 1
$process = $null
if ($app) {
    Start-Process -FilePath ("shell:AppsFolder\" + $app.AppID)
} elseif (Test-Path -LiteralPath $command) {
    $process = Start-Process -FilePath $command -PassThru
} else {
    if ($command.StartsWith('"')) { $file, $rest = $command.Substring(1).Split('"', 2) } else { $file, $rest = $command.Split(' ', 2) }
    $start = @{ FilePath = $file; PassThru = $true }
    if ($rest -and $rest.Trim()) { $start.ArgumentList = $rest.Trim() }
    $process = Start-Process @start
}
ConvertTo-Json -Compress @{ pid = $(if ($process) { $process.Id } else { $null }) }
"""

CLOSE_PROCESSES = r"""
$processes = @(Get-Process -Name __NAME__ -ErrorAction SilentlyContinue)
foreach ($p in $processes) { [void]$p.CloseMainWindow() }
if ($processes) {
    Start-Sleep -Seconds 3
    $processes | Where-Object { -not $_.HasExited } | Stop-Process -Force -ErrorAction SilentlyContinue
}
if ($processes) { 'true' } else { 'false' }
"""


class WinApps:
    @staticmethod
    def installed_apps(container_id: str) -> dict:
        return desktop_json(container_id, INSTALLED_APPS, timeout=60)

    @staticmethod
    def open_app(container_id: str, command: str, display: str = ":1", timeout: float = 4.0) -> dict:
        before = {w["id"] for w in list_windows(container_id)}
        pid = desktop_json(container_id, START_APP.replace("__COMMAND__", q(command)), timeout=30)["pid"]
        deadline = time.monotonic() + timeout
        found = False
        while not found and time.monotonic() < deadline:
            time.sleep(0.3)
            found = any(w["id"] not in before for w in list_windows(container_id))
        alive = (
            pid is None or desktop(container_id, f"[bool](Get-Process -Id {int(pid)} -ErrorAction Ignore)") == "True"
        )
        return {"started": found or alive, "pid": pid, "pid_alive": alive, "window_found": found}

    @staticmethod
    def close_app(container_id: str, target: str, display: str = ":1") -> bool:
        if target.isdigit():
            try:
                return WinWindows.window_close(container_id, target)
            except RuntimeError:
                return False
        name = re.sub(r"\.exe$", "", target, flags=re.IGNORECASE)
        windows = [w for w in list_windows(container_id) if w["app"].lower() == name.lower() or w["title"] == target]
        for w in windows:
            window(container_id, "close", w["id"])
        closed = desktop(container_id, CLOSE_PROCESSES.replace("__NAME__", q(name))) == "true"
        return bool(windows) or closed


def open_url(container_id: str, url: str, display: str = ":1") -> dict:
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", url):
        url = f"https://{url}"
    if not re.match(r"^(https?|file|about|edge):", url, re.IGNORECASE):
        raise ValueError("open_url only opens http(s), file, about and edge URLs")
    desktop(container_id, f"Start-Process -FilePath {q(url)}")
    return {"opened": url}


EXECUTE = r"""
if (Test-Path -LiteralPath __ENV__) { . __ENV__ }
$file = Join-Path $env:TEMP ('zoo-' + [guid]::NewGuid().ToString() + '.ps1')
$body = "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(`$false)`n" + [Text.Encoding]::UTF8.GetString((Read-ZooInput)) +
    "`nif (`$global:LASTEXITCODE) { exit `$global:LASTEXITCODE }"
[IO.File]::WriteAllText($file, $body, [Text.UTF8Encoding]::new($true))
$out = "$file.out"; $err = "$file.err"
try {
    $process = Start-Process -FilePath powershell.exe -NoNewWindow -PassThru -WorkingDirectory $HOME `
        -ArgumentList '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', "`"$file`"" `
        -RedirectStandardOutput $out -RedirectStandardError $err
    $null = $process.Handle
    if ($process.WaitForExit(__TIMEOUT_MS__)) { $code = $process.ExitCode } else { taskkill.exe /T /F /PID $process.Id | Out-Null; $code = 124 }
    [Console]::Out.Write([IO.File]::ReadAllText($out))
    [Console]::Error.Write([IO.File]::ReadAllText($err))
} finally {
    Remove-Item -LiteralPath $file, $out, $err -Force -ErrorAction SilentlyContinue
}
exit $code
"""


class WinShell:
    @staticmethod
    def execute_command(container_id: str, command: str, timeout: int = 30) -> dict:
        """Runs a PowerShell command as the sandbox user, with the sandbox's secrets in its environment."""
        script = EXECUTE.replace("__ENV__", q(ENV_FILE)).replace("__TIMEOUT_MS__", str(int(timeout) * 1000))
        code, out, err = guest(container_id, script, command.encode(), timeout=timeout + 30)
        return {
            "exit_code": code,
            "stdout": out.decode(errors="replace"),
            "stderr": err.decode(errors="replace"),
            "timed_out": code == 124,
        }


def fetch_url(container_id: str, url: str, timeout: int = 20) -> dict:
    result = WinShell.execute_command(container_id, f"curl.exe -sSL --max-time {int(timeout)} {q(url)}", timeout + 5)
    result["stdout"] = result["stdout"][:200000]
    return result


ENTRY = (
    "[ordered]@{ name = $_.Name; type = $(if ($_.PSIsContainer) { 'directory' } else { 'file' }); "
    "size = $(if ($_.PSIsContainer) { 0 } else { $_.Length }) }"
)


def resolved(path: str) -> str:
    return f"$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath({path_of(path)})"


class WinFiles:
    @staticmethod
    def list_files(container_id: str, path: str = "."):
        return guest_json(
            container_id,
            f"ConvertTo-Json -Compress -InputObject @(Get-ChildItem -Force -LiteralPath {path_of(path)} | ForEach-Object {{ {ENTRY} }})",
        )

    @staticmethod
    def get_file_info(container_id: str, path: str):
        return guest_json(
            container_id,
            f"$i = Get-Item -Force -LiteralPath {path_of(path)}\n"
            "ConvertTo-Json -Compress ([ordered]@{ path = $i.FullName; "
            "type = $(if ($i.PSIsContainer) { 'directory' } else { 'regular file' }); "
            "size = $(if ($i.PSIsContainer) { 0 } else { $i.Length }); permissions = $i.Mode; "
            "modified_at = [DateTimeOffset]::new($i.LastWriteTimeUtc).ToUnixTimeSeconds() })",
        )

    @staticmethod
    def create_directory(container_id: str, path: str):
        guest_check(container_id, f"New-Item -ItemType Directory -Force -Path {resolved(path)} | Out-Null")
        return {"success": True, "path": path}

    @staticmethod
    def delete_file(container_id: str, path: str):
        guest_check(container_id, f"Remove-Item -Recurse -Force -LiteralPath {path_of(path)}")
        return {"success": True, "path": path}

    @staticmethod
    def move_file(container_id: str, source: str, destination: str):
        guest_check(
            container_id, f"Move-Item -Force -LiteralPath {path_of(source)} -Destination {resolved(destination)}"
        )
        return {"success": True, "source": source, "destination": destination}

    @staticmethod
    def copy_file(container_id: str, source: str, destination: str):
        guest_check(
            container_id,
            f"Copy-Item -Recurse -Force -LiteralPath {path_of(source)} -Destination {resolved(destination)}",
        )
        return {"success": True, "source": source, "destination": destination}

    @staticmethod
    def read_file(container_id: str, path: str):
        return guest_check(container_id, f"[Console]::Out.Write([IO.File]::ReadAllText({resolved(path)}))")

    @staticmethod
    def write_file(container_id: str, path: str, content: str):
        name = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if name in ("", ".", ".."):
            raise ValueError("A filename is required")
        data = content.encode("utf-8")
        guest_check(container_id, f"[IO.File]::WriteAllBytes({resolved(path)}, (Read-ZooInput))", data)
        return {"success": True, "path": path, "size": len(data)}
