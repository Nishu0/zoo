"""The `zoo` command (pip install 'zoo-sdk[cli]').

    zoo login                       store the server URL and an API key
    zoo create --kind desktop       create a sandbox and wait until it runs
    zoo ls                          list sandboxes
    zoo exec <id> -- make test      run a command, streaming its output; exits with its exit code
    zoo shell <id>                  an interactive shell
    zoo view <id>                   open the live screen in a browser
    zoo cp ./a <id>:b / <id>:b ./a  copy files in and out
    zoo logs <id> [-f]              recent tool calls, then (with -f) its events as they happen
    zoo rm <id>                     delete a sandbox

    zoo backups                     list database backups (admin only)

ZOO_URL and ZOO_API_KEY override the stored login; --json prints machine-readable output."""

import asyncio
import json
import os
import re
import shutil
import signal
import sys
import tomllib
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, NoReturn, TypeVar

try:
    import typer
except ImportError:  # pragma: no cover
    sys.exit("the zoo command needs its extras: pip install 'zoo-sdk[cli]'")

from zoo_sdk.client import Sandbox, Zoo
from zoo_sdk.errors import ZooError

app = typer.Typer(help="Zoo sandboxes from the terminal.", no_args_is_help=True, pretty_exceptions_enable=False)

T = TypeVar("T")

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "zoo" / "config.toml"
# `<sandbox id>:<path>`; not a Windows drive (C:\...)
REMOTE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_-]{2,}):(.*)$")

Json = Annotated[bool, typer.Option("--json", help="Print JSON.")]


def stored() -> dict[str, str]:
    try:
        with open(CONFIG, "rb") as f:
            return {k: str(v) for k, v in tomllib.load(f).get("default", {}).items()}
    except FileNotFoundError:
        return {}


def client() -> Zoo:
    config = stored()
    url = os.environ.get("ZOO_URL") or config.get("url")
    key = os.environ.get("ZOO_API_KEY") or config.get("api_key")
    if not key:
        fail("not logged in: run `zoo login` (or set ZOO_API_KEY)")
    return Zoo(api_key=key, base_url=url, workspace=os.environ.get("ZOO_WORKSPACE") or config.get("workspace"))


def fail(message: str, code: int = 1) -> NoReturn:
    typer.secho(message, fg="red", err=True)
    raise typer.Exit(code)


def show(data: Any):
    typer.echo(json.dumps(data, indent=2, default=str))


def run(fn: Callable[[], T]) -> T:  # noqa: UP047 (the SDK supports Python 3.11)
    """Runs a command body, turning API errors into a message and exit code 1."""
    try:
        return fn()
    except ZooError as e:
        hint = f"\n  {e.hint}" if e.hint else ""
        fail(f"error: {e.message} ({e.code}){hint}")


@app.command()
def login(
    url: Annotated[str, typer.Option(prompt="Zoo URL", help="The API's URL.")] = "http://localhost:8000",
    api_key: Annotated[str, typer.Option(prompt="API key", hide_input=True, help="From Settings → API keys.")] = "",
):
    """Store the server URL and an API key in ~/.config/zoo/config.toml."""
    zoo = Zoo(api_key=api_key, base_url=url, max_retries=1)
    me = run(lambda: zoo._json("GET", "/auth/me"))
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"[default]\nurl = {json.dumps(url.rstrip('/'))}\napi_key = {json.dumps(api_key)}\n")
    os.chmod(CONFIG, 0o600)
    typer.echo(f"Logged in to {url} as {me.get('email', me.get('id'))}.")


@app.command()
def create(
    kind: Annotated[str, typer.Option(help="desktop, code, browser, macos or windows.")] = "desktop",
    name: Annotated[str | None, typer.Option(help="A name; one is made up by default.")] = None,
    label: Annotated[list[str], typer.Option(help="key=value; repeat for more.")] = [],  # noqa: B006
    wait: Annotated[bool, typer.Option(help="Wait until it runs.")] = True,
    as_json: Json = False,
):
    """Create a sandbox."""
    labels = dict(item.split("=", 1) for item in label)
    box = run(lambda: client().create(name=name, kind=kind, labels=labels, wait=wait))  # type: ignore[arg-type]
    if as_json:
        show(box.data)
    else:
        typer.echo(f"{box.id}  {box.name}  {box.status}")


@app.command("ls")
def list_sandboxes(
    label: Annotated[list[str], typer.Option(help="Only those with key=value; repeat for more.")] = [],  # noqa: B006
    as_json: Json = False,
):
    """List sandboxes."""
    labels = dict(item.split("=", 1) for item in label)
    boxes = run(lambda: client().sandboxes(labels))
    if as_json:
        return show([box.data for box in boxes])
    if not boxes:
        typer.echo("No sandboxes. Create one with `zoo create --kind desktop`.")
        return
    rows = [("ID", "NAME", "KIND", "STATUS")] + [(b.id, b.name, b.kind, b.status) for b in boxes]
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    for row in rows:
        typer.echo("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip())


@app.command("exec", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def execute(
    sandbox_id: str,
    command: Annotated[list[str], typer.Argument(help="The command, after --.")],
    timeout: Annotated[int, typer.Option(help="Seconds before it is stopped (at most 300).")] = 300,
):
    """Run a command in a sandbox, streaming its output; exits with the command's exit code."""
    line = command[0] if len(command) == 1 else " ".join(_quote(part) for part in command)

    def go() -> int:
        box = client().get(sandbox_id)
        for chunk in box.exec(line, timeout=timeout, stream=True):
            if chunk.stream == "exit":
                if chunk.timed_out:
                    typer.secho(f"timed out after {timeout}s", fg="red", err=True)
                return chunk.exit_code if chunk.exit_code is not None else 1
            out = sys.stdout if chunk.stream == "stdout" else sys.stderr
            out.write(chunk.data)
            out.flush()
        return 1

    raise typer.Exit(run(go))


def _quote(part: str) -> str:
    import shlex

    return shlex.quote(part)


@app.command()
def shell(sandbox_id: str):
    """An interactive shell in a sandbox (Linux)."""
    if not sys.stdin.isatty():
        fail("zoo shell needs a terminal; use `zoo exec` for scripts")
    zoo = client()
    ticket = run(lambda: zoo._json("POST", f"/sandboxes/{sandbox_id}/terminal-ticket"))["ticket"]
    size = shutil.get_terminal_size()
    url = zoo.config.url(f"/sandboxes/{sandbox_id}/terminal").replace("http", "ws", 1)
    url += f"?ticket={ticket}&cols={size.columns}&rows={size.lines}"
    code = asyncio.run(_terminal(url))
    raise typer.Exit(code or 0)


async def _terminal(url: str) -> int | None:
    import termios
    import tty

    from websockets.asyncio.client import connect

    loop = asyncio.get_running_loop()
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    code: int | None = None
    async with connect(url, max_size=None) as ws:
        tty.setraw(fd)
        try:
            keys: asyncio.Queue[bytes] = asyncio.Queue()
            loop.add_reader(fd, lambda: keys.put_nowait(os.read(fd, 4096)))

            def resized(*_: object):
                size = shutil.get_terminal_size()
                message = json.dumps({"type": "resize", "cols": size.columns, "rows": size.lines})
                loop.create_task(ws.send(message))

            signal.signal(signal.SIGWINCH, resized)

            async def typing():
                while True:
                    await ws.send(await keys.get())

            sender = asyncio.create_task(typing())
            try:
                async for message in ws:
                    if isinstance(message, bytes):
                        os.write(sys.stdout.fileno(), message)
                    elif json.loads(message).get("type") == "exit":
                        code = json.loads(message).get("code")
                        break
            finally:
                sender.cancel()
                loop.remove_reader(fd)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    return code


@app.command()
def view(
    sandbox_id: str,
    control: Annotated[bool, typer.Option(help="Allow typing and clicking, not just watching.")] = True,
    minutes: Annotated[int, typer.Option(help="How long the link works.")] = 60,
    print_only: Annotated[bool, typer.Option("--print", help="Print the link instead of opening it.")] = False,
):
    """Open a sandbox's live screen in the browser (through a short-lived viewer link)."""
    link = run(lambda: client().get(sandbox_id).viewer_link("control" if control else "view", minutes))
    if print_only or not webbrowser.open(link["url"]):
        typer.echo(link["url"])


@app.command()
def cp(source: str, destination: str):
    """Copy a file into (./a <id>:b) or out of (<id>:b ./a) a sandbox. Remote paths are relative to its home."""
    src, dst = REMOTE.match(source), REMOTE.match(destination)
    if bool(src) == bool(dst):
        fail("one side must be <sandbox id>:<path> and the other a local path")
    zoo = client()
    if src:
        data = run(lambda: zoo.get(src.group(1)).read_file(src.group(2)))
        target = Path(destination)
        if target.is_dir():
            target = target / Path(src.group(2)).name
        target.write_bytes(data)
        typer.echo(f"{len(data)} bytes → {target}")
    else:
        assert dst is not None
        local = Path(source)
        if not local.is_file():
            fail(f"{source} isn't a file")
        path = dst.group(2) or local.name
        if path.endswith("/"):
            path += local.name
        result = run(lambda: zoo.get(dst.group(1)).write_file(path, local.read_bytes()))
        typer.echo(f"{result.get('size')} bytes → {dst.group(1)}:{result.get('path', path)}")


@app.command()
def logs(
    sandbox_id: str,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Then follow its events.")] = False,
    limit: Annotated[int, typer.Option(help="How many recent tool calls.")] = 20,
    as_json: Json = False,
):
    """A sandbox's recent tool calls and, with -f, its events (state changes) as they happen."""
    zoo = client()
    box: Sandbox = run(lambda: zoo.get(sandbox_id))
    for e in reversed(run(lambda: box.executions(limit))):
        if as_json:
            typer.echo(json.dumps(e, default=str))
        else:
            mark = {"completed": "✓", "failed": "✗"}.get(e.get("status", ""), "…")
            typer.echo(f"{e['created_at']}  {mark} {e['tool_name']}  {(e.get('input') or '')[:120]}")
    if not follow:
        return
    try:
        for event in zoo.events(sandbox_id=sandbox_id):
            if as_json:
                typer.echo(json.dumps(event, default=str))
            else:
                typer.echo(f"{event['created_at']}  {event['type']}  {json.dumps(event['data'])[:160]}")
    except KeyboardInterrupt:
        pass


@app.command("rm")
def remove(sandbox_ids: list[str], wait: Annotated[bool, typer.Option(help="Wait until it's gone.")] = False):
    """Delete sandboxes."""
    zoo = client()
    for sandbox_id in sandbox_ids:
        run(lambda sandbox_id=sandbox_id: zoo.get(sandbox_id).delete(wait=wait))
        typer.echo(f"deleted {sandbox_id}")


@app.command()
def backups(as_json: Json = False):
    """List database backups in object storage (admin only)."""
    found = run(lambda: client()._json("GET", "/admin/backups"))
    if as_json:
        return show(found)
    for item in found:
        typer.echo(f"{item.get('key')}  {item.get('size')} bytes")


def main():
    app()


if __name__ == "__main__":
    main()
