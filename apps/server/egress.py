"""Host-side network policy. Each sandbox's policy goes to its host's egress daemon (`zoo-guest -egress`, see
guest/egress.go) as one JSON file; the daemon proxies and filters the sandbox's traffic outside the sandbox, so an
agent with root or admin inside can't undo it. The backends (docker, macos, windows) write and remove the files."""

import json
import re
import socket

PROXY_PORT = 15128
DNS_PORT = 15353


def proxied(default_action: str, rules: list[tuple[str, str, str]]) -> bool:
    """Whether names decide anything, so web traffic and DNS must go through the daemon."""
    return default_action == "deny" or any(rule_type == "domain" for rule_type, _, _ in rules)


def file_name(runtime_id: str) -> str:
    return re.sub(r"[^\w.-]", "_", runtime_id) + ".json"


def addresses(host: str) -> list[str]:
    """The API's addresses as the API itself resolves them; a sandbox must not be the one to say where it is."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return sorted({str(info[4][0]) for info in infos})


def always(endpoint: tuple[str, int] | None, resolved: list[str] | None = None) -> list[dict]:
    if endpoint is None:
        return []
    host, port = endpoint
    return [{"ip": ip, "port": port} for ip in (resolved if resolved is not None else addresses(host))]


def policy(
    runtime_id: str,
    addrs: list[str],
    default_action: str,
    allow_dns: bool,
    rules: list[tuple[str, str, str]],
    endpoints: list[dict],
) -> bytes:
    return json.dumps(
        {
            "id": runtime_id,
            "addrs": addrs,
            "default": default_action,
            "dns": allow_dns,
            "rules": [{"type": t, "value": v, "effect": e} for t, v, e in rules],
            "always": endpoints,
        }
    ).encode()
