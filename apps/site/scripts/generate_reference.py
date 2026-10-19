#!/usr/bin/env python3
"""Generate the docs Reference section (and the release badge) from code.

The following pages in apps/site/src/content/docs/docs/reference/ are produced here.
Conventions and performance targets are maintained separately:

  api.md            the checked-in SDK OpenAPI artifact
  tools.md          apps/server/registry.py — TOOLS, KINDS, PERMISSIONS
  sdk.md            docstrings in sdk/python/zoo_sdk (ast-parsed)
  configuration.md  every environment variable the code reads
  errors.md         every HTTP status the API actually returns
  cli.md            commands and signatures parsed from the SDK CLI

Run from anywhere: `uv run python apps/site/scripts/generate_reference.py`
(prebuild for the site; needs the repo's Python environment, no database).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "apps/site/src/content/docs/docs/reference"
VERSION_FILE = ROOT / "apps/site/src/generated/version.json"

GENERATED_NOTE = (
    ":::note\n"
    "Generated from the code by `apps/site/scripts/generate_reference.py` — do not edit by hand.\n"
    "Regenerate with `bun run docs:gen` from `site/`.\n"
    ":::"
)

# ---------------------------------------------------------------------------
# version badge
# ---------------------------------------------------------------------------


def write_version() -> str:
    try:
        tag = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "--match=v*"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        version = tag.removeprefix("v")
    except subprocess.CalledProcessError:
        version = "edge"
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(json.dumps({"version": version}, indent=2) + "\n")
    print(f"version: {version}")
    return version


# ---------------------------------------------------------------------------
# shared markdown helpers
# ---------------------------------------------------------------------------


def esc(text: str) -> str:
    """Escape pipes for markdown tables."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def schema_type(schema: dict) -> str:
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "anyOf" in schema:
        return " or ".join(schema_type(s) for s in schema["anyOf"])
    if "allOf" in schema and len(schema["allOf"]) == 1:
        return schema_type(schema["allOf"][0])
    t = schema.get("type", "any")
    if t == "array":
        return f"{schema_type(schema.get('items', {}))}[]"
    if t == "integer" and schema.get("format") == "int64":
        return "int"
    return t


def field_rows(schema: dict, spec: dict) -> list[tuple[str, str, str, str]]:
    """(name, type, required, description) for an object schema, one level deep."""
    if "$ref" in schema:
        schema = resolve(schema["$ref"], spec)
    required = set(schema.get("required", []))
    rows = []
    for name, prop in schema.get("properties", {}).items():
        desc = (prop.get("description") or "").strip().split("\n")[0]
        t = schema_type(prop)
        if t.endswith("[]"):
            item = prop.get("items", {})
            if "$ref" in item:
                t = f"{item['$ref'].split('/')[-1]}[]"
        rows.append((name, t, "yes" if name in required else "no", desc))
    return rows


def fields_table(schema: dict, spec: dict) -> str:
    rows = field_rows(schema, spec)
    if not rows:
        return ""
    out = ["| Field | Type | Required | Description |", "| --- | --- | --- | --- |"]
    for name, t, req, desc in rows:
        out.append(f"| `{esc(name)}` | `{esc(t)}` | {req} | {esc(desc)} |")
    return "\n".join(out) + "\n"


def first_paragraph(text: str) -> str:
    text = (text or "").strip()
    return text.split("\n\n")[0]


# ---------------------------------------------------------------------------
# REST API reference — from the OpenAPI schema
# ---------------------------------------------------------------------------

GROUPS = OrderedDict(
    [
        ("sandboxes", "Sandboxes"),
        ("servers", "Servers"),
        ("profiles", "App profiles"),
        ("vault", "Vault and secrets"),
        ("api-keys", "API keys"),
        ("domains", "Domains"),
        ("admin", "Admin"),
        ("auth", "Auth"),
        ("nodes", "Nodes"),
        ("guest", "Guest agent"),
        ("tools", "Tools"),
        ("monitoring", "Monitoring"),
    ]
)

METHOD_ORDER = ["get", "post", "put", "patch", "delete"]


def resolve(ref: str, spec: dict) -> dict:
    node: dict = spec
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def group_of(path: str) -> str:
    name = path.removeprefix("/v1").strip("/").split("/")[0]
    if name.startswith("sandboxes"):
        return "Sandboxes"
    if name.startswith("profiles"):
        return "App profiles"
    for prefix, label in GROUPS.items():
        if name == prefix or name.startswith(prefix + "/"):
            return label
    return name.capitalize() if name else "Other"


def api_spec() -> dict:
    return json.loads((ROOT / "sdk/typescript/openapi.json").read_text())


def gen_api() -> None:
    spec = api_spec()

    out = [
        "---",
        "title: REST API",
        "description: Every HTTP endpoint the Zoo API serves, generated from its OpenAPI schema.",
        "---",
        "",
        GENERATED_NOTE,
        "",
        "Generated from `sdk/typescript/openapi.json`, the shared API/SDK schema artifact.",
        "Regenerate that artifact after API changes before publishing this reference.",
        "The API serves its schema at `/docs` on a running server. Every request is",
        "authenticated according to its declared security requirements; auth and public health routes differ:",
    ]
    schemes = spec.get("components", {}).get("securitySchemes", {})
    for name, scheme in schemes.items():
        out.append(f"- **{name}**: {scheme.get('type')} — {(scheme.get('description') or '').strip()}")
    out += [
        "",
        "The Python SDK wraps these endpoints; see [SDK](/docs/reference/sdk/).",
    ]

    grouped: dict[str, list[tuple[str, str, dict]]] = {}
    for path, methods in spec["paths"].items():
        label = group_of(path)
        for method, op in methods.items():
            if method not in METHOD_ORDER or not isinstance(op, dict):
                continue
            grouped.setdefault(label, []).append((path, method, op))

    for label in [g for g in GROUPS.values()] + sorted(set(grouped) - set(GROUPS.values())):
        if label not in grouped:
            continue
        endpoints = sorted(grouped[label], key=lambda e: (e[0], METHOD_ORDER.index(e[1])))
        out += ["", f"## {label}", ""]

        # summary table
        out += ["| Endpoint | Purpose |", "| --- | --- |"]
        for path, method, op in endpoints:
            summary = op.get("summary") or op.get("operationId") or ""
            out.append(
                f"| [`{method.upper()} {path}`](#{method}-{path.strip('/').replace('/', '').replace('{', '').replace('}', '')}) | {esc(summary)} |"
            )

        # details
        for path, method, op in endpoints:
            out += ["", f"### `{method.upper()} {path}`", ""]
            desc = first_paragraph(op.get("description") or op.get("summary") or "")
            if desc:
                out += [desc, ""]
            params = op.get("parameters") or []
            if params:
                out += ["| Parameter | In | Type | Required | Description |", "| --- | --- | --- | --- | --- |"]
                for p in params:
                    schema = p.get("schema", {})
                    out.append(
                        f"| `{esc(p['name'])}` | {p['in']} | `{esc(schema_type(schema))}` | "
                        f"{'yes' if p.get('required') else 'no'} | {esc(first_paragraph(p.get('description') or ''))} |"
                    )
                out.append("")
            body = (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema")
            if body:
                table = fields_table(body, spec)
                if table:
                    out += ["**Request body**", "", table]
            responses = op.get("responses") or {}
            codes = [c for c in responses if c not in ("default",)]
            if codes:
                rows = []
                for code in sorted(codes):
                    r = responses[code]
                    rows.append(f"| `{code}` | {esc(first_paragraph(r.get('description') or ''))} |")
                out += ["| Response | |", "| --- | --- |", *rows, ""]

    # component schemas appendix
    schemas = spec.get("components", {}).get("schemas", {})
    if schemas:
        out += ["", "## Schemas", "", "Request and response bodies, as the API defines them.", ""]
        for name, schema in schemas.items():
            out += ["", f"### `{name}`", ""]
            desc = first_paragraph(schema.get("description") or "")
            if desc:
                out += [desc, ""]
            table = fields_table(schema, spec)
            if table:
                out += [table]
            else:
                out += [f"Type: `{esc(schema_type(schema))}`", ""]

    write("api.md", "\n".join(out) + "\n")
    print(f"api.md: {len(spec['paths'])} paths")


# ---------------------------------------------------------------------------
# Tool reference — from apps/server/registry.py
# ---------------------------------------------------------------------------


def gen_tools() -> None:
    sys.path.insert(0, str(ROOT))
    from apps.server.registry import KINDS, PERMISSIONS, TOOLS

    kinds_all = ", ".join(f"`{k}`" for k in KINDS)
    out = [
        "---",
        "title: Tools",
        "description: Every tool a sandbox exposes over REST, MCP and the SDK, generated from apps/server/registry.py.",
        "---",
        "",
        GENERATED_NOTE,
        "",
        "Every tool is registered once in `apps/server/registry.py` and exposed the same way over",
        "REST, MCP and the SDK. `GET /v1/tools?kind=desktop` returns the same data live.",
        "",
        f"Sandbox kinds: {kinds_all}. A `desktop`, `macos` or `windows` sandbox offers every",
        "category; `browser` and `code` restrict them (columns below).",
    ]

    by_category: dict[str, list] = {}
    for tool in TOOLS.values():
        by_category.setdefault(tool.category, []).append(tool)

    for category, tools in sorted(by_category.items()):
        out += ["", f"## {category}", "", "| Tool | Arguments | Permission | Kinds |", "| --- | --- | --- | --- |"]
        for t in sorted(tools, key=lambda t: t.name):
            args = ", ".join(
                f"{p.name}: {getattr(p.annotation, '__name__', str(p.annotation))}"
                + ("" if p.default is __import__("inspect").Parameter.empty else f" = {p.default!r}")
                for p in t.params
            )
            perm = f"{t.permission}.{t.action}"
            perm_desc = PERMISSIONS.get((t.permission, t.action), "")
            supported = [k for k, cats in KINDS.items() if cats is None or t.category in cats]
            kinds = "all" if len(supported) == len(KINDS) else ", ".join(f"`{k}`" for k in supported)
            out.append(f"| `{t.name}` | `{esc(args) if args else '—'}` | `{perm}` — {esc(perm_desc)} | {kinds} |")

    out += [
        "",
        "## Permissions",
        "",
        "Checked by the API before every tool call; a denied call returns `403`.",
        "",
        "| Permission | Covers |",
        "| --- | --- |",
    ]
    for (perm, action), desc in PERMISSIONS.items():
        out.append(f"| `{perm}.{action}` | {esc(desc)} |")

    write("tools.md", "\n".join(out) + "\n")
    print(f"tools.md: {len(TOOLS)} tools")


# ---------------------------------------------------------------------------
# SDK reference — from docstrings in sdk/python
# ---------------------------------------------------------------------------


def sig_of(node: ast.FunctionDef) -> str:
    args = ast.unparse(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"({args}){returns}"


def gen_sdk() -> None:
    pkg = ROOT / "sdk/python/zoo_sdk"
    out = [
        "---",
        "title: Python SDK",
        "description: zoo_sdk classes and methods, generated from the SDK's docstrings.",
        "---",
        "",
        GENERATED_NOTE,
        "",
        "```bash",
        "pip install zoo-sdk            # or: pip install ./sdk/python",
        "pip install 'zoo-sdk[cli]'     # plus the zoo command",
        "```",
        "",
        "```python",
        "from zoo_sdk import Zoo",
        "",
        "zoo = Zoo()  # reads ZOO_API_KEY and ZOO_URL",
        'with zoo.sandbox(kind="desktop") as box:  # deleted on exit, also on error',
        '    for chunk in box.exec("uname -a", stream=True):',
        '        print(chunk.data, end="")',
        "```",
        "",
        "`AsyncZoo` is the asyncio twin (`async with await zoo.sandbox(kind=...) as box`). Every call has a timeout",
        "(30 s unless it passes `timeout=`) and is retried with exponential backoff when that is safe: connection failures",
        "and 429 always; 5xx and timeouts for reads and for creates, which carry an automatic `Idempotency-Key`. Errors",
        "are `ZooError` subclasses with the API's [error code](/docs/reference/errors/). The TypeScript SDK",
        "(`npm install @zoo/sdk`) has the same methods in camelCase; see `sdk/typescript/README.md`.",
    ]

    for path in sorted(pkg.glob("*.py")):
        # private helpers, and the CLI, which has its own page
        if path.name.startswith("_") and path.name != "__init__.py" or path.name == "cli.py":
            continue
        tree = ast.parse(path.read_text())
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        if not classes:
            continue
        module = "zoo_sdk" if path.name == "__init__.py" else f"zoo_sdk.{path.stem}"
        for cls in classes:
            out += ["", f"## `{module}.{cls.name}`", ""]
            doc = ast.get_docstring(cls)
            if doc:
                out += [first_paragraph(doc), ""]
            init = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
            if init:
                out += [f"### `{cls.name}.__init__`", "", "```python", f"{cls.name}{sig_of(init)}", "```", ""]
            methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]
            if methods:
                out += ["| Method | |", "| --- | --- |"]
                for m in methods:
                    docline = first_paragraph(ast.get_docstring(m) or "")
                    out.append(f"| [`{m.name}()`](#{cls.name.lower()}{m.name.lower()}) | {esc(docline)} |")
                for m in methods:
                    out += ["", f"### `{cls.name}.{m.name}` {{#{cls.name.lower()}{m.name.lower()}}}", ""]
                    doc = ast.get_docstring(m)
                    if doc:
                        first = first_paragraph(doc)
                        out += [first, ""]
                    out += ["```python", f"{m.name}{sig_of(m)}", "```", ""]

    out += [
        "",
        "## Errors",
        "",
        "Any non-2xx response raises `zoo_sdk.ZooError` with the status code and the API's",
        "stable `code`, `message`, `hint`, `docs_url` and `request_id`; see [Errors](/docs/reference/errors/). Any tool can be called as a method",
        "(`box.window_focus(window_id=...)`) or with `box.tool(name, **args)`.",
    ]

    write("sdk.md", "\n".join(out) + "\n")
    print(f"sdk.md: {sum(1 for _ in out)} lines")


# ---------------------------------------------------------------------------
# Configuration reference — from environment reads in the code
# ---------------------------------------------------------------------------

ENV_SCAN_DIRS = ["apps/server", "gateway", "node", "egress", "vm-control"]
REMOVED_SETTINGS = {"ZOO_GATEWAY", "ZOO_GUEST_URL", "ZOO_GUEST_REMOTE_URL", "ZOO_NODE_ENDPOINTS", "ZOO_SSH_DIR"}
DESCRIPTIONS = {
    "JWT_SECRET": "Signs sessions.",
    "ZOO_SECRETS_KEY": "Wraps workspace keys that encrypt secrets, app profiles and VNC passwords. Back it up.",
    "ZOO_SECRETS_KEY_PREVIOUS": "Old keys, comma-separated, kept only until `make rotate-secrets` has run.",
    "ZOO_KMS": "Wrap workspace data keys with an external KMS: `aws:<key ARN>`, `gcp:…` or `vault:<mount>/<key>`.",
    "ZOO_OBJECT_STORE": "Required. `s3://<bucket>[/<prefix>]`: profiles, portable home snapshots and database backups.",
    "ZOO_S3_ENDPOINT": "S3-compatible endpoint for `ZOO_OBJECT_STORE` (MinIO, R2).",
    "ZOO_S3_REGION": "Region for `ZOO_OBJECT_STORE`.",
    "AWS_ACCESS_KEY_ID": "Credentials for `ZOO_OBJECT_STORE` (with `AWS_SECRET_ACCESS_KEY`).",
    "AWS_SECRET_ACCESS_KEY": "Credentials for `ZOO_OBJECT_STORE` (with `AWS_ACCESS_KEY_ID`).",
    "FORWARDED_ALLOW_IPS": "Proxies whose `X-Forwarded-For` the API trusts. Behind Caddy, set it to Caddy's address.",
    "ADMIN_EMAILS": "Comma-separated emails allowed to use `/admin/*` and Domains.",
    "CORS_ORIGINS": "Allowed dashboard origins.",
    "ZOO_API_URL": "API URL the dashboard calls, read at container start. Also baked into installer commands and join tokens.",
    "DATABASE_URL": "A `postgresql://` URL (required).",
    "ZOO_DB_POOL_SIZE": "Postgres connections each process keeps at most.",
    "ZOO_MIGRATE": "`0` skips the migrations `scripts/start.sh` runs before the API starts.",
    "ZOO_ROLE": "Python process role: all, api or worker. The gateway is a separate native Go process.",
    "ZOO_GATEWAY_PEERS": "Gateway peer DNS name, not a URL: gateway in Compose, headless service DNS on Kubernetes, localhost for source development.",
    "ZOO_GATEWAY_IP": "This gateway replica's own IP; must occur in peer DNS. Use the downward pod IP on Kubernetes or explicit loopback for source development.",
    "ZOO_GATEWAY_GRPC_PORT": "Authenticated TLS RPC port shared by API clients and gateway replicas; default 7444.",
    "ZOO_GATEWAY_HTTP_PORT": "Guest websocket and health listener; default 8001. Public ingress routes /guest/connect here.",
    "ZOO_GATEWAY_PEER_HTTP_PORT": "Private TLS HTTP listener for gateway-to-gateway relays; default 8002. Do not expose it as a public API.",
    "ZOO_NODE_HOST": "Public node endpoint hostname or IP without a port; defaults to the API URL's hostname. Set separately for a node load balancer.",
    "ZOO_METRICS_PORT": "Serves Prometheus metrics on this port at `/metrics`.",
    "ZOO_NODE_PORT": "Native gateway's public zoo-node mTLS port; default 7443. Distinct from private gateway RPCs.",
    "ZOO_NODE_DIST": "zoo-node builds served to installers and pushed to nodes on another version.",
    "ZOO_VM_CONTROL_TRUST_DOMAIN": "Installation-specific SPIFFE trust domain; separate from the node-enrollment CA.",
    "ZOO_VM_CONTROL_CLIENT_CERT": "Controller leaf certificate file with the exact api or worker URI matching ZOO_ROLE; do not reuse one role's leaf for the other.",
    "ZOO_VM_CONTROL_CLIENT_KEY": "Private key file for the process's VM-control controller leaf.",
    "ZOO_VM_CONTROL_NODE_CA": "Public node/server CA file used to verify VM-control frontends; not the gateway CA.",
    "ZOO_VM_CONTROL_NODE_CA_KEY": "Node signing key file mounted only in the dedicated issuer, not normal API/worker or frontend pods.",
    "ZOO_VM_CONTROL_SERVER_NAME": "Expected VM-control frontend DNS SAN; node URI/generation is also verified.",
    "ZOO_VM_CONTROL_NAMESPACE": "Kubernetes sandbox/VM-control namespace used for authenticated discovery.",
    "ZOO_VM_CONTROL_SELECTOR": "Exact installation VM-control DaemonSet Pod label selector.",
    "ZOO_VM_CONTROL_DAEMONSET": "Expected VM-control DaemonSet owner name.",
    "ZOO_VM_CONTROL_SERVICE_ACCOUNT": "Expected VM-control node enrollment/discovery ServiceAccount.",
    "ZOO_VM_CONTROL_NODE_NAME": "Provisioned single-Docker-host node identity name.",
    "ZOO_VM_CONTROL_NODE_UID": "Immutable generation UUID for the single Docker host.",
    "ZOO_VM_CONTROL_DOCKER_URL": "Explicit HTTPS frontend URL for the authenticated single-host VM controller.",
    "ZOO_VM_CONTROL_DOCKER_NAMESPACE": "Actual Docker containerd namespace, normally moby; not the Zoo sandbox network.",
    "ZOO_MAX_SNAPSHOTS": "Snapshots kept per sandbox.",
    "ZOO_NAMESPACE": "Namespace for Kubernetes sandbox pods and home claims.",
    "ZOO_STORAGE_CLASS": "StorageClass of home claims; defaults to `zoo-local`.",
    "ZOO_KUBERNETES_HOME_SIZE": "Size of each home claim.",
    "ZOO_SNAPSHOT_CLASS": "CSI snapshot class used before home archives are uploaded; defaults to `zoo-snapshots`.",
    "ZOO_IDLE_MEMORY_MB_DESKTOP": "Idle memory request for desktop/browser pods in MiB; defaults to 700.",
    "ZOO_IDLE_MEMORY_MB_CODE": "Idle memory request for code pods in MiB; defaults to 250.",
    "ZOO_SPARE_VOLUMES": "Unbound default-size home claims kept ready per Kubernetes sandbox node; defaults to 4.",
    "ZOO_PAUSE_MINUTES": "How long a stopped Linux sandbox stays paused (resumable) before it is deleted to its volume; defaults to 30.",
    "ZOO_API_WORKERS": "API processes (uvicorn workers); defaults to the CPU count. Ignored while reloading from source.",
    "ZOO_KUBERNETES_IMAGE_PULL_SECRET": "Pull secret for sandbox and helper pods.",
    "ZOO_KUBERNETES_EGRESS_SELECTOR": "Label selector of the egress daemon's pods.",
    "ZOO_KUBERNETES_EGRESS_NAMESPACE": "Namespace of the egress daemon (default: the sandbox namespace).",
    "ZOO_KUBERNETES_PROBE_IMAGE": "What the requirements check starts on each node.",
    "ZOO_KUBERNETES_CONTEXT": "Kubeconfig context to use in-cluster-less setups.",
    "ZOO_KUBERNETES_START_TIMEOUT": "Seconds to wait for a sandbox pod to start.",
    "ZOO_KUBERNETES_HELPER_IMAGE": "Helper image for snapshot and move jobs.",
    "ZOO_EMBED_ORIGINS": "Origins (space-separated) that may put a viewer link's page in an iframe; none when unset.",
    "ZOO_DOCS_URL": "Base of the errors page that error responses link to as `docs_url` (`<url>#<code>`).",
    "ZOO_URL": "The API URL the Python SDK and the zoo CLI use; http://localhost:8000 by default.",
    "ZOO_MAX_VIEWER_LINK_HOURS": "Longest a viewer link may last.",
    "ZOO_EVENT_RETENTION_DAYS": "How long events (`GET /events`, webhooks) and their deliveries are kept.",
    "ZOO_WEBHOOK_ALLOW_PRIVATE": "`1` lets webhooks reach private addresses.",
    "ZOO_WEBHOOK_BACKOFF_SECONDS": "Seconds between a failed webhook delivery's retries, comma-separated; it fails after the last.",
    "ZOO_NETWORK": "Docker network shared by the API and sandboxes. Compose sets it to `zoo`.",
    "ZOO_RUNTIME": "Default Linux backend: `docker` or `kube`. Kubernetes requires Kata; Docker follows `ZOO_ISOLATION`.",
    "ZOO_ISOLATION": "Docker isolation on local and remote engines: `auto` prefers Kata, `kata` requires Kata, `runc` uses ordinary containers.",
    "ZOO_EGRESS_IMAGE": "Egress controller image deployed on enrolled Linux Docker engines.",
    "ZOO_GUEST_DARWIN_BINARY": "Path of the darwin zoo-guest build to install into macOS VMs.",
    "ZOO_MACOS_BASE": "zoovm VM that macOS sandboxes are cloned from.",
    "ZOO_MACOS_USER": "Guest user for macOS sandboxes.",
    "ZOO_MACOS_CPUS": "CPUs of each macOS sandbox.",
    "ZOO_MACOS_MEMORY_MB": "Memory of each macOS sandbox.",
    "ZOO_WINDOWS_BASE": "Hyper-V VM that Windows sandboxes are cloned from.",
    "ZOO_WINDOWS_CPUS": "CPUs of each Windows sandbox.",
    "ZOO_WINDOWS_MEMORY_MB": "Memory of each Windows sandbox.",
    "ZOO_WINDOWS_MAX_VMS": "Windows sandboxes running at once on each server.",
    "ZOO_WINDOWS_SWITCH": "Hyper-V switch for sandboxes on Windows Server hosts (no Default Switch there).",
    "ZOO_VERSION": "Release whose images compose runs. `install.sh` pins it; Compose or Helm upgrades it.",
    "ZOO_REGISTRY": "Where compose pulls images from.",
    "ZOO_SANDBOX_IMAGE": "Image for Linux `desktop` sandboxes.",
    "ZOO_BROWSER_IMAGE": "Firefox-only image for Linux `browser` sandboxes.",
    "ZOO_CODE_IMAGE": "Image for `code` sandboxes.",
    "ZOO_BROWSER_HOME": "Start page for `browser` sandboxes.",
    "ZOO_DOMAIN": "Always-allowed domain for Caddy.",
    "ZOO_PUBLIC_IP": "This server's public address(es), used to check that a domain's DNS points here.",
    "ZOO_GRAFANA_DOMAIN": "Grafana hostname behind Caddy.",
    "ZOO_INSTALL_URL": "Optional installer source override; by default enrollment uses installers served by this API image.",
    "ZOO_VM_E2E": "Test-only: enables the nightly macOS and Windows VM end-to-end runs.",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "Enables OpenTelemetry traces, metrics and logs.",
    "OTEL_SERVICE_NAME": "Service name in traces.",
    "HOST": "API server bind address. The API image sets `0.0.0.0`.",
    "PORT": "API server port.",
    "RELOAD": "Dev autoreload. The API image sets `0`.",
    "VAULT_ADDR": "HashiCorp Vault address, for the `vault:` KMS.",
    "VAULT_TOKEN": "HashiCorp Vault token, for the `vault:` KMS.",
    "VAULT_NAMESPACE": "HashiCorp Vault namespace, for the `vault:` KMS.",
    "GOOGLE_APPLICATION_CREDENTIALS": "Google Cloud credentials, for the `gcp:` KMS.",
    "KUBECONFIG": "Kubeconfig used outside a cluster.",
    "ZOO_TEST_DATABASE_URL": "Test-only: run the suite against Postgres.",
}


def scan_env() -> dict[str, str | None]:
    """Canonical deployment settings read by Python and native components."""
    found: dict[str, str | None] = {}
    targets = [ROOT / "main.py"]
    native = []
    for directory in ENV_SCAN_DIRS:
        base = ROOT / directory
        if base.is_dir():
            targets += [path for path in base.rglob("*.py") if "__pycache__" not in str(path)]
            native += [path for path in base.rglob("*.go") if not path.name.endswith("_test.go")]
    for path in targets:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in ("os.environ.get", "os.getenv"):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    default = ast.unparse(node.args[1]) if len(node.args) > 1 else None
                    found.setdefault(node.args[0].value, default)
            elif (
                path == ROOT / "apps/server/vm_control.py"
                and isinstance(node, ast.Call)
                and ast.unparse(node.func) == "setting"
            ):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    found.setdefault("ZOO_VM_CONTROL_" + node.args[0].value, None)
            elif (
                isinstance(node, ast.Subscript)
                and ast.unparse(node.value) == "os.environ"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                found.setdefault(node.slice.value, None)
    for path in native:
        text = path.read_text()
        for match in re.finditer(r'os\.(?:Getenv|LookupEnv)\("([A-Z][A-Z0-9_]+)"\)', text):
            found.setdefault(match[1], None)
        for match in re.finditer(r'\bport\("([A-Z][A-Z0-9_]+)",\s*(\d+)\)', text):
            found[match[1]] = match[2]
    return {name: default for name, default in found.items() if name not in REMOVED_SETTINGS}


def py_literal(raw: str | None) -> str:
    if raw is None:
        return "—"
    return raw.strip().strip('"').strip("'") or "—"


def gen_config() -> None:
    env = scan_env()
    missing_desc = sorted(
        n for n in env if n not in DESCRIPTIONS and not n.startswith("AWS_") and n not in ("PATH", "PYTHONPATH")
    )
    out = [
        "---",
        "title: Configuration",
        "description: Canonical deployment settings from Python and native components.",
        "---",
        "",
        GENERATED_NOTE,
        "",
        "Canonical deployment settings, parsed from Python and native source. Removed interfaces",
        "are not configurable: guest addresses are derived, and the gateway is a native service.",
        "Set deployment values",
        "in `/opt/zoo/.env` (the installer writes `JWT_SECRET` and `ZOO_SECRETS_KEY`) or in the",
        "chart's documented values on Kubernetes. Peer RPCs use TLS on 7444; peer HTTP uses",
        "TLS on 8002; guest websockets use 8001; native node enrollment uses mTLS on 7443.",
        "",
        "| Variable | Default | Purpose |",
        "| --- | --- | --- |",
    ]
    for name in sorted(env):
        default = py_literal(env[name])
        if default.startswith("os."):  # computed default
            default = "computed"
        out.append(f"| `{name}` | `{esc(default)}` | {esc(DESCRIPTIONS.get(name, 'Internal setting.'))} |")
    if missing_desc:
        out += ["", f"Undocumented in this table: {', '.join(f'`{m}`' for m in missing_desc)}.", ""]
    write("configuration.md", "\n".join(out) + "\n")
    print(f"configuration.md: {len(env)} variables")


# ---------------------------------------------------------------------------
# Error reference — from the statuses the OpenAPI schema actually declares
# ---------------------------------------------------------------------------


def gen_errors() -> None:
    spec = api_spec()
    usage: dict[str, list[str]] = {}
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if not isinstance(op, dict):
                continue
            for code in op.get("responses") or {}:
                if code != "default":
                    usage.setdefault(code, [])
                    entry = f"`{method.upper()} {path}`"
                    if entry not in usage[code]:
                        usage[code].append(entry)

    # FastAPI doesn't declare statuses raised with HTTPException at runtime, so
    # scan the handlers too: any `status_code=<code>` the server can emit.
    raised = re.compile(r"status_code\s*=\s*(\d{3})")
    for target in (ROOT / "apps/server").glob("*.py"):
        for m in raised.finditer(target.read_text(errors="ignore")):
            usage.setdefault(m.group(1), [])

    def sort_key(c: str) -> tuple:
        try:
            return (0, int(c))
        except ValueError:
            return (1, c)

    def where(code: str) -> str:
        places = usage[code]
        if not places:
            return "raised by the handlers"
        return esc(", ".join(places[:3]) + (f", +{len(places) - 3} more" if len(places) > 3 else ""))

    out = [
        "---",
        "title: Errors",
        "description: Every HTTP status the Zoo API returns, generated from its OpenAPI schema and handlers, with a fix for each.",
        "---",
        "",
        GENERATED_NOTE,
        "",
        "Every error the API returns has one shape:",
        "",
        "```json",
        '{"error": {"code": "sandbox_not_found", "message": "sandbox not found",',
        '           "hint": "`GET /v1/sandboxes` lists this workspace\'s sandboxes; ids are case-sensitive.",',
        '           "docs_url": "…/errors#sandbox_not_found", "request_id": "3f9c…"}}',
        "```",
        "",
        "Match on `code`: it is stable within an API version, while `message` may change. A `500` never carries",
        "exception text; report its `request_id` (also in the `X-Request-Id` header) and the operator finds the",
        "traceback in the logs. The SDKs raise a `ZooError` subclass per status (`NotFoundError`, `ConflictError`,",
        "`RateLimitError`, …) carrying `code`, `message`, `hint` and `docs_url`.",
        "",
        "## Codes",
        "",
    ]
    definitions = {}
    for node in ast.parse((ROOT / "apps/server/errors.py").read_text()).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            if node.target.id in ("CODES", "HINTS"):
                definitions[node.target.id] = ast.literal_eval(node.value)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "HINTS" for target in node.targets
        ):
            definitions["HINTS"] = ast.literal_eval(node.value)
    codes, hints = definitions["CODES"], definitions["HINTS"]

    for code, (status, meaning) in codes.items():
        out += [f"### `{code}`", "", f"HTTP `{status}`. {meaning}", ""]
        if code in hints:
            out += [f"Fix: {hints[code]}", ""]
    out += [
        "## Statuses",
        "",
        "Where each HTTP status is declared or raised.",
        "",
        "| Status | Where it comes from |",
        "| --- | --- |",
    ]
    for code in sorted(usage, key=sort_key):
        out.append(f"| `{code}` | {where(code)} |")

    write("errors.md", "\n".join(out) + "\n")
    print(f"errors.md: {len(usage)} statuses")


def gen_cli() -> None:
    source = ROOT / "sdk/python/zoo_sdk/cli.py"
    tree = ast.parse(source.read_text())
    aliases = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    out = [
        "---",
        "title: CLI",
        "description: Sandbox commands and flags generated from the Python SDK CLI.",
        "---",
        "",
        GENERATED_NOTE,
        "",
        "Install with `pip install 'zoo-sdk[cli]'`, then run `zoo login`.",
        "`ZOO_URL` and `ZOO_API_KEY` override the stored login; `ZOO_WORKSPACE` selects a workspace.",
        "",
    ]
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        decorator = next(
            (
                item
                for item in node.decorator_list
                if isinstance(item, ast.Call)
                and isinstance(item.func, ast.Attribute)
                and isinstance(item.func.value, ast.Name)
                and item.func.value.id == "app"
                and item.func.attr == "command"
            ),
            None,
        )
        if decorator is None:
            continue
        name = ast.literal_eval(decorator.args[0]) if decorator.args else node.name.replace("_", "-")
        out += [f"## `zoo {name}`", "", ast.get_docstring(node) or "", ""]
        parameters = node.args.args
        defaults = [None] * (len(parameters) - len(node.args.defaults)) + list(node.args.defaults)
        rows = []
        usage = ["zoo", name]
        for parameter, default in zip(parameters, defaults, strict=True):
            annotation = parameter.annotation
            if isinstance(annotation, ast.Name):
                annotation = aliases.get(annotation.id, annotation)
            metadata = None
            if isinstance(annotation, ast.Subscript) and isinstance(annotation.slice, ast.Tuple):
                type_node, *extras = annotation.slice.elts
                metadata = next((item for item in extras if isinstance(item, ast.Call)), None)
            else:
                type_node = annotation
            type_name = ast.unparse(type_node) if type_node else "Any"
            option = (
                metadata is not None and isinstance(metadata.func, ast.Attribute) and metadata.func.attr == "Option"
            ) or default is not None
            help_text = (
                next((ast.literal_eval(item.value) for item in metadata.keywords if item.arg == "help"), "")
                if metadata
                else ""
            )
            flags = (
                [ast.literal_eval(item) for item in metadata.args if isinstance(item, ast.Constant)] if metadata else []
            )
            if option:
                flag = flags[0] if flags else "--" + parameter.arg.replace("_", "-")
                if type_name == "bool" and not flags:
                    flag += "/--no-" + parameter.arg.replace("_", "-")
                display = ", ".join(flags) if flags else flag
                value = "" if type_name == "bool" else " VALUE"
                usage.append(f"[{flag}{value}]")
            else:
                display = parameter.arg.upper() + ("..." if type_name.startswith("list[") else "")
                usage.append(("-- " if parameter.arg == "command" else "") + display)
            default_text = ast.unparse(default) if default is not None else "required"
            rows.append(f"| `{esc(display)}` | `{esc(type_name)}` | `{esc(default_text)}` | {esc(help_text)} |")
        out += ["```text", " ".join(usage), "```", ""]
        if rows:
            out += ["| Argument / option | Type | Default | Description |", "| --- | --- | --- | --- |", *rows, ""]
    out += [
        "## Deployment and backups",
        "",
        "`zoo backups [--json]` lists existing backups through `GET /v1/admin/backups` with an admin key.",
        "There is no restore CLI or live destructive restore API. Restore remains a deliberate offline",
        "administrator operation with API, worker and gateway stopped; see [backups](/docs/guides/domain-https-backups/).",
        "Use `docker compose` or `helm` directly for deployment and upgrades, not sandbox CLI server commands.",
        "See [upgrading](/docs/guides/upgrading/) and the [install guide](/docs/get-started/install/).",
    ]
    write("cli.md", "\n".join(out) + "\n")


def write(name: str, text: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(text)
    print(f"wrote {OUT / name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--version-file", type=Path, default=VERSION_FILE)
    arguments = parser.parse_args()
    OUT = arguments.output_dir
    VERSION_FILE = arguments.version_file
    write_version()
    gen_api()
    gen_tools()
    gen_sdk()
    gen_config()
    gen_errors()
    gen_cli()
