"""accessibility_tree: one window's UI elements as an indented outline, with screen boxes to click.

Each OS returns the same pre-order node list, `{"app", "window", "nodes": [{d, role, name, value, states, box}],
"truncated"}`, from the guest's `a11y` service: AT-SPI on Linux, AX on macOS, UI Automation on Windows. This module turns the
list into the outline agents read."""

import json
import re

from server.errors import ZooError
from server.guest import hub

DEFAULT_NODES = 300
MAX_NODES = 2000
MAX_VALUE = 200
# containers that carry no meaning of their own when unnamed; their children move up a level
GENERIC = {
    "",
    "filler",
    "panel",
    "section",
    "group",
    "pane",
    "unknown",
    "redundant object",
    "scroll pane",
    "viewport",
    "layered pane",
    "custom",
    "splitter",
}
MARKED = {"focused", "selected", "checked"}


def check(max_nodes: int):
    if not 1 <= max_nodes <= MAX_NODES:
        raise ValueError(f"max_nodes must be between 1 and {MAX_NODES}")


def role_name(role: str) -> str:
    """'push button', 'PushButton' and 'AXPushButton' all become 'push button'."""
    role = re.sub(r"^AX", "", role or "")
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", role).lower().strip()


def quote(text: str) -> str:
    text = " ".join(str(text).split())
    if len(text) > MAX_VALUE:
        text = text[:MAX_VALUE] + "…"
    return json.dumps(text, ensure_ascii=False)


def render(result: dict) -> dict:
    """The outline: one line per element, `role "name" = "value" [states] @(x,y wxh)`, indented by depth. Unnamed
    generic containers are dropped and their children moved up."""
    lines: list[str] = []
    kept: list[int] = []
    for node in result.get("nodes") or []:
        depth = node.get("d", 0)
        while kept and kept[-1] >= depth:
            kept.pop()
        role = role_name(node.get("role", ""))
        name = (node.get("name") or "").strip()
        value = node.get("value")
        value = "" if value is None else str(value).strip()
        states = node.get("states") or []
        if not name and not value and role in GENERIC and not MARKED & set(states):
            continue
        line = role or "element"
        if name:
            line += f" {quote(name)}"
        if value and value != name:
            line += f" = {quote(value)}"
        if states:
            line += f" [{', '.join(states)}]"
        box = node.get("box")
        if box and len(box) == 4:
            x, y, w, h = (int(v) for v in box)
            line += f" @({x},{y} {w}x{h})"
        lines.append("  " * len(kept) + line)
        kept.append(depth)
    return {
        "app": result.get("app", ""),
        "window": result.get("window", ""),
        "tree": "\n".join(lines),
        "elements": len(lines),
        "truncated": bool(result.get("truncated")),
    }


def accessibility_tree(container_id: str, app: str = "", title: str = "", max_nodes: int = DEFAULT_NODES) -> dict:
    """The UI elements of one window: the active one, or the first whose app and title contain `app` and `title`.
    Each line is `role "name" = "value" [states] @(x,y wxh)`; click the middle of the box to press an element.
    Faster and more exact than reading a screenshot when the app exposes accessibility (most GTK, Qt, browser,
    macOS and Windows apps do)."""
    check(max_nodes)
    guest = hub.for_runtime(container_id)
    if guest is None or not guest.has("a11y"):
        raise ZooError("guest_unavailable", "the sandbox's guest agent is not connected")
    result, _ = guest.call("a11y", {"app": app, "title": title, "max_nodes": max_nodes}, timeout=30)
    return render(result)
