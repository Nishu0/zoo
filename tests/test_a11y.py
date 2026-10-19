import pytest

from apps.server import a11y
from apps.server.errors import ZooError

TREE = {
    "app": "Firefox",
    "window": "Zoo",
    "truncated": False,
    "nodes": [
        {"d": 0, "role": "frame", "name": "Zoo", "box": [0, 0, 1280, 720]},
        {"d": 1, "role": "panel", "name": ""},
        {"d": 2, "role": "push button", "name": "Back", "states": ["focused"], "box": [4, 40, 32, 32]},
        {"d": 2, "role": "entry", "name": "Address", "value": "zoo.dev", "states": ["editable"]},
        {"d": 1, "role": "AXStaticText", "name": "Hello\nworld"},
        {"d": 1, "role": "CheckBox", "name": "", "states": ["checked"]},
    ],
}


def test_render_drops_unnamed_containers_and_lifts_their_children():
    out = a11y.render(TREE)
    assert out["tree"].splitlines() == [
        'frame "Zoo" @(0,0 1280x720)',
        '  push button "Back" [focused] @(4,40 32x32)',
        '  entry "Address" = "zoo.dev" [editable]',
        '  static text "Hello world"',
        "  check box [checked]",
    ]
    assert out["elements"] == 5 and out["app"] == "Firefox" and not out["truncated"]


def test_long_values_are_cut():
    out = a11y.render({"nodes": [{"d": 0, "role": "text", "name": "", "value": "x" * 500}]})
    assert len(out["tree"]) < 220 and out["tree"].endswith('…"')


def test_max_nodes_is_bounded():
    with pytest.raises(ValueError):
        a11y.check(0)
    with pytest.raises(ValueError):
        a11y.check(a11y.MAX_NODES + 1)


class Guest:
    def __init__(self, services):
        self.services = services
        self.calls = []

    def has(self, service):
        return service in self.services

    def call(self, op, args, payload=b"", timeout=600):
        self.calls.append((op, args))
        return TREE, b""


def test_linux_reads_the_guest(monkeypatch):
    guest = Guest({"a11y"})
    monkeypatch.setattr(a11y.hub, "for_runtime", lambda rid: guest)
    out = a11y.accessibility_tree("c1", app="fire", max_nodes=10)
    assert guest.calls == [("a11y", {"app": "fire", "title": "", "max_nodes": 10})]
    assert out["tree"].startswith('frame "Zoo"')


def test_linux_without_the_service_says_why(monkeypatch):
    monkeypatch.setattr(a11y.hub, "for_runtime", lambda rid: Guest({"exec"}))
    with pytest.raises(ZooError, match="guest agent is not connected"):
        a11y.accessibility_tree("c1")
