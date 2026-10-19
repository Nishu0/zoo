"""Authenticated, generation-bound native Kata VM control and node certificate issuance."""

import argparse
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import APIRouter, FastAPI, Request
from starlette.concurrency import run_in_threadpool

from apps.server import errors, runtime
from apps.server.errors import ZooError

PROTOCOL = "zoo-kata-vm-control/1"
KATA_VERSION = "4.2.0"
KATA_REVISION = "c7351e797efff8bfc6bd73da0eb1909be12e2cfe"
ENROLL_AUDIENCE = "zoo-vm-control-enroll"
IDENTIFIER = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\Z")
NATIVE_ID = re.compile(r"[a-f0-9]{64}\Z")
NODE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
OPERATION = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{15,127}\Z")
TIMEOUT = 20.0
HINT = "Install the pinned shim and authenticated vm-control service; do not use container task pause."


@dataclass(frozen=True)
class VMState:
    state: str
    revision: int
    native_id: str
    namespace: str
    generation: str
    operation_id: str = ""


def setting(name: str) -> str:
    value = os.environ.get(f"ZOO_VM_CONTROL_{name}", "")
    if not value:
        raise ZooError("unavailable", f"ZOO_VM_CONTROL_{name} is required", HINT)
    return value


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise ZooError("timeout", "VM control deadline exceeded", HINT)
    return value


def kube_request(method: str, path: str, deadline: float, body: Any = None, params: dict | None = None) -> dict:
    try:
        return runtime.kube.api.request(method, path, body=body, params=params, timeout=min(5.0, remaining(deadline)))
    except httpx.TimeoutException:
        raise ZooError("timeout", "VM control inventory deadline exceeded", HINT) from None
    except (runtime.kube.KubeError, httpx.RequestError):
        raise ZooError("unavailable", "VM control inventory unavailable", HINT) from None


def identity(node_name: str, node_uid: str) -> str:
    domain = setting("TRUST_DOMAIN")
    if (
        not isinstance(node_name, str)
        or not isinstance(node_uid, str)
        or not NODE.fullmatch(domain)
        or not NODE.fullmatch(node_name)
        or not IDENTIFIER.fullmatch(node_uid)
    ):
        raise ZooError("bad_request", "Invalid VM control node identity")
    return f"spiffe://{domain}/vm-control/node/{node_name}/{node_uid}"


def owned_control_pod(pod: dict) -> bool:
    metadata, spec = pod.get("metadata", {}), pod.get("spec", {})
    labels = metadata.get("labels", {})
    selector = setting("SELECTOR")
    pairs = [part.split("=", 1) for part in selector.split(",")]
    if not pairs or any(len(pair) != 2 or not pair[0] or not pair[1] for pair in pairs):
        raise ZooError("unavailable", "VM control selector must contain explicit equality labels", HINT)
    return (
        not metadata.get("deletionTimestamp")
        and labels.get("zoo.dev/vm-control") == "true"
        and all(labels.get(pair[0]) == pair[1] for pair in pairs)
        and spec.get("serviceAccountName") == setting("SERVICE_ACCOUNT")
        and any(
            owner.get("kind") == "DaemonSet" and owner.get("name") == setting("DAEMONSET") and owner.get("controller")
            for owner in metadata.get("ownerReferences", [])
        )
    )


def discover(sandbox_id: str, runtime_id: str, config: dict, deadline: float) -> tuple[dict, str, int]:
    if not IDENTIFIER.fullmatch(sandbox_id):
        raise ZooError("bad_request", "Sandbox UUID is required for VM control")
    saved = config.get("vm_control")
    if saved is not None and (not isinstance(saved, dict) or saved.get("runtime_id") != runtime_id):
        raise ZooError("conflict", "VM control runtime generation changed")
    if runtime.NAME == "kube":
        match = re.fullmatch(r"k8s:([a-z0-9.-]+)/([a-z0-9.-]+)", runtime_id)
        if not match or match[2] != f"zoo-sandbox-{sandbox_id}":
            raise ZooError("conflict", "VM control Pod identity differs from sandbox")
        namespace, name = match[1], match[2]
        pod = kube_request("GET", f"/api/v1/namespaces/{namespace}/pods/{name}", deadline)
        metadata, spec, status = pod.get("metadata", {}), pod.get("spec", {}), pod.get("status", {})
        labels = metadata.get("labels", {})
        node_name = spec.get("nodeName", "")
        if (
            metadata.get("name") != name
            or metadata.get("namespace") != namespace
            or metadata.get("deletionTimestamp")
            or labels.get("app.kubernetes.io/managed-by") != "zoo"
            or labels.get("zoo.dev/sandbox") != sandbox_id
            or spec.get("runtimeClassName") != "kata"
            or not NODE.fullmatch(node_name)
            or status.get("phase") != "Running"
            or (config.get("node_name") is not None and config["node_name"] != node_name)
        ):
            raise ZooError("conflict", "Current Pod is not the owned running Kata sandbox")
        node = kube_request("GET", f"/api/v1/nodes/{quote(node_name, safe='')}", deadline)
        if node.get("metadata", {}).get("name") != node_name or node.get("metadata", {}).get("deletionTimestamp"):
            raise ZooError("conflict", "Current sandbox node identity is unavailable")
        node_uid = node.get("metadata", {}).get("uid", "")
        target = {
            "node_name": node_name,
            "node_uid": node_uid,
            "sandbox_id": sandbox_id,
            "pod_namespace": namespace,
            "pod_name": name,
            "pod_uid": metadata.get("uid", ""),
        }
        if not IDENTIFIER.fullmatch(target["pod_uid"]):
            raise ZooError("conflict", "Pod generation UUID is missing")
        pods = kube_request(
            "GET",
            f"/api/v1/namespaces/{setting('NAMESPACE')}/pods",
            deadline,
            params={"labelSelector": setting("SELECTOR"), "fieldSelector": f"spec.nodeName={node_name}"},
        ).get("items", [])
        candidates = [
            candidate
            for candidate in pods
            if owned_control_pod(candidate)
            and candidate.get("spec", {}).get("nodeName") == node_name
            and candidate.get("status", {}).get("phase") == "Running"
            and any(
                item.get("type") == "Ready" and item.get("status") == "True"
                for item in candidate.get("status", {}).get("conditions", [])
            )
        ]
        if len(candidates) != 1:
            raise ZooError("unavailable", "Exactly one ready VM controller on the owning node is required", HINT)
        try:
            host = str(ipaddress.ip_address(candidates[0]["status"]["podIP"]))
        except (KeyError, ValueError):
            raise ZooError("unavailable", "Node-local VM controller address is missing", HINT) from None
        port = 9444
    elif runtime.NAME == "docker":
        if not NATIVE_ID.fullmatch(runtime_id):
            raise ZooError("bad_request", "Docker VM control requires the full container ID")
        target = {
            "node_name": setting("NODE_NAME"),
            "node_uid": setting("NODE_UID"),
            "sandbox_id": sandbox_id,
            "container_id": runtime_id,
        }
        endpoint = urlsplit(setting("DOCKER_URL"))
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.path not in ("", "/")
            or endpoint.query
            or endpoint.fragment
        ):
            raise ZooError(
                "unavailable", "Docker VM control must use an explicitly configured HTTPS node endpoint", HINT
            )
        try:
            host, port = str(ipaddress.ip_address(endpoint.hostname)), endpoint.port or 9444
        except ValueError:
            raise ZooError(
                "unavailable", "Docker VM controller must use a configured local-host IP address", HINT
            ) from None
    else:
        raise ZooError("not_supported", "Native VM control requires Docker or Kubernetes")
    identity(target["node_name"], target["node_uid"])
    if saved is not None:
        if (
            saved.get("target") != target
            or not NATIVE_ID.fullmatch(saved.get("native_id", ""))
            or not NATIVE_ID.fullmatch(saved.get("generation", ""))
        ):
            raise ZooError("conflict", "Persisted VM ownership differs from the current node or Pod generation")
        target["native_id"] = saved["native_id"]
        target["generation"] = saved["generation"]
    return target, host, port


def request_state(target: dict, host: str, port: int, deadline: float, mutation: dict | None = None) -> VMState:
    try:
        role = os.environ.get("ZOO_ROLE", "")
        if role not in ("api", "worker"):
            raise ZooError("unavailable", "Explicit API or worker VM controller role is required", HINT)
        client_leaf = x509.load_pem_x509_certificate(Path(setting("CLIENT_CERT")).read_bytes())
        client_names = client_leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if client_names.get_values_for_type(x509.UniformResourceIdentifier) != [
            f"spiffe://{setting('TRUST_DOMAIN')}/vm-control/controller/{role}"
        ]:
            raise ZooError("forbidden", "VM controller credential does not match its explicit role")
        context = ssl.create_default_context(cafile=setting("NODE_CA"))
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(setting("CLIENT_CERT"), setting("CLIENT_KEY"))
        with (
            socket.create_connection((host, port), timeout=min(5.0, remaining(deadline))) as raw,
            context.wrap_socket(raw, server_hostname=setting("SERVER_NAME")) as secured,
        ):
            certificate = x509.load_der_x509_certificate(secured.getpeercert(binary_form=True) or b"")
            names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            if names.get_values_for_type(x509.UniformResourceIdentifier) != [
                identity(target["node_name"], target["node_uid"])
            ]:
                raise ZooError("forbidden", "VM control server has the wrong node generation identity")
            connection = http.client.HTTPSConnection(host, port, timeout=remaining(deadline), context=context)
            connection.sock, connection.auto_open = secured, 0
            try:
                secured.settimeout(remaining(deadline))
                body = json.dumps({"target": target, **(mutation or {})}).encode()
                connection.request(
                    "PUT" if mutation else "POST",
                    "/v1/vm/state",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                chunks, length = [], 0
                while length <= 8192:
                    secured.settimeout(remaining(deadline))
                    chunk = response.read1(min(1024, 8193 - length))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    length += len(chunk)
                if response.status != 200:
                    code = "conflict" if response.status == 409 else "unavailable"
                    raise ZooError(code, "Native VM control rejected the operation", HINT)
                if length > 8192:
                    raise ZooError("upstream_error", "VM control response exceeds its bound")
                result = json.loads(b"".join(chunks))
            finally:
                connection.close()
    except TimeoutError:
        raise ZooError("timeout", "Native VM control did not answer within its deadline", HINT) from None
    except (OSError, ssl.SSLError, http.client.HTTPException, ValueError, x509.ExtensionNotFound):
        raise ZooError("unavailable", "Authenticated native VM control is unavailable", HINT) from None
    if not isinstance(result, dict) or result.get("target") != target or not isinstance(result.get("vm"), dict):
        raise ZooError("upstream_error", "VM control response ownership is invalid")
    state = result["vm"]
    namespace = "k8s.io" if runtime.NAME == "kube" else setting("DOCKER_NAMESPACE")
    if (
        state.get("protocol") != PROTOCOL
        or state.get("kata_version") != KATA_VERSION
        or state.get("kata_revision") != KATA_REVISION
        or state.get("namespace") != namespace
        or not isinstance(state.get("sandbox_id"), str)
        or not NATIVE_ID.fullmatch(state["sandbox_id"])
        or (target.get("native_id") is not None and target["native_id"] != state["sandbox_id"])
        or not isinstance(state.get("generation"), str)
        or not NATIVE_ID.fullmatch(state["generation"])
        or (target.get("generation") is not None and target["generation"] != state["generation"])
        or state.get("state") not in ("running", "paused")
        or type(state.get("revision")) is not int
        or not 0 <= state["revision"] < 2**64
        or (mutation is not None and state.get("operation_id") != mutation["operation_id"])
    ):
        raise ZooError("conflict", "Pinned native VM generation or verified state differs")
    return VMState(
        state["state"],
        state["revision"],
        state["sandbox_id"],
        namespace,
        state["generation"],
        state.get("operation_id", ""),
    )


def binding(sandbox_id: str, runtime_id: str, config: dict) -> dict:
    """Call once after boot; persist this result in config.vm_control before lifecycle mutations."""
    if "vm_control" in config:
        raise ZooError("conflict", "A VM control binding already exists; do not rebind a live generation")
    deadline = time.monotonic() + TIMEOUT
    target, host, port = discover(sandbox_id, runtime_id, config, deadline)
    state = request_state(target, host, port, deadline)
    return {"runtime_id": runtime_id, "target": target, "native_id": state.native_id, "generation": state.generation}


def control(sandbox_id: str, runtime_id: str, config: dict, mutation: dict | None = None) -> VMState:
    if not isinstance(config.get("vm_control"), dict):
        raise ZooError("conflict", "Persisted VM control generation binding is required")
    deadline = time.monotonic() + TIMEOUT
    target, host, port = discover(sandbox_id, runtime_id, config, deadline)
    return request_state(target, host, port, deadline, mutation)


def current_state(sandbox_id: str, runtime_id: str, config: dict) -> VMState:
    return control(sandbox_id, runtime_id, config)


def mutate(
    sandbox_id: str, runtime_id: str, config: dict, state: str, operation_id: str, expected_revision: int
) -> VMState:
    if (
        not OPERATION.fullmatch(operation_id)
        or type(expected_revision) is not int
        or not 0 <= expected_revision < 2**64
    ):
        raise ZooError("bad_request", "Explicit operation ID and unsigned revision are required")
    return control(
        sandbox_id,
        runtime_id,
        config,
        {
            "desired_state": state,
            "operation_id": operation_id,
            "expected_revision": expected_revision,
        },
    )


def pause(sandbox_id: str, runtime_id: str, config: dict, *, operation_id: str, expected_revision: int) -> VMState:
    return mutate(sandbox_id, runtime_id, config, "paused", operation_id, expected_revision)


def resume(sandbox_id: str, runtime_id: str, config: dict, *, operation_id: str, expected_revision: int) -> VMState:
    return mutate(sandbox_id, runtime_id, config, "running", operation_id, expected_revision)


def issue_node(csr_pem: str, node_name: str, node_uid: str) -> str:
    """The issuer is API-only; nodes receive no CA private keys."""
    node_identity = identity(node_name, node_uid)
    try:
        csr = x509.load_pem_x509_csr(csr_pem.encode())
        public = csr.public_key()
        if (
            not csr.is_signature_valid
            or not isinstance(public, ec.EllipticCurvePublicKey)
            or not isinstance(public.curve, ec.SECP256R1)
        ):
            raise ValueError("P-256 signed CSR required")
        ca = x509.load_pem_x509_certificate(Path(setting("NODE_CA")).read_bytes())
        key = serialization.load_pem_private_key(Path(setting("NODE_CA_KEY")).read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ) != ca.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo):
            raise ValueError("issuer key does not match authority")
        now = datetime.now(UTC)
        if (
            ca.not_valid_before_utc > now
            or ca.not_valid_after_utc <= now + timedelta(hours=12)
            or not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        ):
            raise ValueError("valid issuing authority required")
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_name)]))
            .issuer_name(ca.subject)
            .public_key(public)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=12))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName(setting("SERVER_NAME")), x509.UniformResourceIdentifier(node_identity)]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        return certificate.public_bytes(serialization.Encoding.PEM).decode()
    except (ValueError, OSError, x509.ExtensionNotFound):
        raise ZooError("unavailable", "Node certificate issuer or CSR is invalid", HINT) from None


def enroll(body: dict, token: str) -> dict:
    if runtime.NAME == "docker":
        if set(body) != {"node_name", "node_uid", "csr"} or not all(isinstance(value, str) for value in body.values()):
            raise ZooError("bad_request", "Single-host Docker certificate enrollment required")
        try:
            expected = Path(setting("DOCKER_ENROLL_TOKEN")).read_text().strip()
        except OSError:
            raise ZooError("unavailable", "Docker node enrollment credential unavailable", HINT) from None
        if (
            len(expected) < 32
            or len(expected) > 128
            or not hmac.compare_digest(token.encode(), expected.encode())
            or body["node_name"] != setting("NODE_NAME")
            or body["node_uid"] != setting("NODE_UID")
        ):
            raise ZooError("forbidden", "Pinned single-host node enrollment identity required")
        return {
            "node_name": body["node_name"],
            "node_uid": body["node_uid"],
            "certificate": issue_node(body["csr"], body["node_name"], body["node_uid"]),
        }
    if runtime.NAME != "kube" or set(body) != {"node_name", "pod_name", "pod_uid", "csr"}:
        raise ZooError("bad_request", "Pod-bound Kubernetes certificate enrollment required")
    if (
        not all(isinstance(value, str) for value in body.values())
        or not NODE.fullmatch(body["node_name"])
        or not NODE.fullmatch(body["pod_name"])
        or not IDENTIFIER.fullmatch(body["pod_uid"])
    ):
        raise ZooError("bad_request", "Invalid enrollment identity")
    deadline = time.monotonic() + TIMEOUT
    review = kube_request(
        "POST",
        "/apis/authentication.k8s.io/v1/tokenreviews",
        deadline,
        body={
            "apiVersion": "authentication.k8s.io/v1",
            "kind": "TokenReview",
            "spec": {"token": token, "audiences": [ENROLL_AUDIENCE]},
        },
    ).get("status", {})
    user = review.get("user", {})
    extras = user.get("extra", {})
    namespace = setting("NAMESPACE")
    if (
        review.get("authenticated") is not True
        or review.get("error")
        or ENROLL_AUDIENCE not in review.get("audiences", [])
        or user.get("username") != f"system:serviceaccount:{namespace}:{setting('SERVICE_ACCOUNT')}"
        or extras.get("authentication.kubernetes.io/pod-name") != [body["pod_name"]]
        or extras.get("authentication.kubernetes.io/pod-uid") != [body["pod_uid"]]
    ):
        raise ZooError("forbidden", "Verified Pod-bound enrollment audience and identity are required")
    pod = kube_request("GET", f"/api/v1/namespaces/{namespace}/pods/{body['pod_name']}", deadline)
    if (
        not owned_control_pod(pod)
        or pod.get("metadata", {}).get("uid") != body["pod_uid"]
        or pod.get("spec", {}).get("nodeName") != body["node_name"]
    ):
        raise ZooError("forbidden", "Enrollment Pod is not the owned controller on this node")
    node = kube_request("GET", f"/api/v1/nodes/{body['node_name']}", deadline)
    node_uid = node.get("metadata", {}).get("uid", "")
    if node.get("metadata", {}).get("deletionTimestamp"):
        raise ZooError("conflict", "Enrollment node is being deleted")
    return {
        "node_name": body["node_name"],
        "node_uid": node_uid,
        "certificate": issue_node(body["csr"], body["node_name"], node_uid),
    }


def enrollment_router() -> APIRouter:
    """Mount under /v1 in the API, never in the worker or guest listener."""
    router = APIRouter()

    @router.post("/vm-control/certificates")
    async def certificates(request: Request):
        token = request.headers.get("Authorization", "")
        if not token.startswith("Bearer ") or not 0 < len(token[7:]) <= 16384:
            raise ZooError("unauthenticated", "Pod-bound enrollment token required")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 8192:
                raise ZooError("too_large", "Enrollment request exceeds its bound")
        try:
            body = json.loads(data)
        except ValueError:
            raise ZooError("bad_request", "Enrollment requires a JSON object") from None
        if not isinstance(body, dict):
            raise ZooError("bad_request", "Enrollment requires a JSON object")
        return await run_in_threadpool(enroll, body, token[7:])

    return router


def issuer_app() -> FastAPI:
    """Dedicated API-only issuer: no database, migrations or host-readiness startup dependency."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    errors.register(app)
    app.include_router(enrollment_router(), prefix="/v1")
    return app


def bootstrap_pki(directory: str, trust_domain: str, issuer_server_name: str) -> None:
    """Offline install hook: separate public CAs, API/worker client keys; refuses existing output."""
    if not NODE.fullmatch(trust_domain) or not NODE.fullmatch(issuer_server_name):
        raise ZooError("bad_request", "Canonical installation trust domain and issuer DNS name required")
    output = Path(directory)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    now = datetime.now(UTC)

    def write(name: str, data: bytes):
        with os.fdopen(os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
            handle.write(data)

    write("docker-enrollment-token", secrets.token_urlsafe(32).encode())

    for authority in ("node", "controller"):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{trust_domain} vm-control {authority}")])
        ca = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
            .sign(key, hashes.SHA256())
        )
        write(f"{authority}-ca.pem", ca.public_bytes(serialization.Encoding.PEM))
        write(
            f"{authority}-ca-key.pem",
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            ),
        )
        if authority == "node":
            private = ec.generate_private_key(ec.SECP256R1())
            leaf = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_server_name)]))
                .issuer_name(subject)
                .public_key(private.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(days=90))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(
                    x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True
                )
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(
                    x509.SubjectAlternativeName(
                        [
                            x509.DNSName(issuer_server_name),
                            x509.UniformResourceIdentifier(f"spiffe://{trust_domain}/vm-control/issuer/api"),
                        ]
                    ),
                    critical=False,
                )
                .sign(key, hashes.SHA256())
            )
            write("issuer.pem", leaf.public_bytes(serialization.Encoding.PEM))
            write(
                "issuer-key.pem",
                private.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                ),
            )
        if authority == "controller":
            for role in ("api", "worker"):
                private = ec.generate_private_key(ec.SECP256R1())
                leaf = (
                    x509.CertificateBuilder()
                    .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, role)]))
                    .issuer_name(subject)
                    .public_key(private.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(now - timedelta(minutes=1))
                    .not_valid_after(now + timedelta(days=90))
                    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                    .add_extension(
                        x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True
                    )
                    .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
                    .add_extension(
                        x509.SubjectAlternativeName(
                            [x509.UniformResourceIdentifier(f"spiffe://{trust_domain}/vm-control/controller/{role}")]
                        ),
                        critical=False,
                    )
                    .sign(key, hashes.SHA256())
                )
                write(f"{role}.pem", leaf.public_bytes(serialization.Encoding.PEM))
                write(
                    f"{role}-key.pem",
                    private.private_bytes(
                        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                    ),
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline VM-control certificate provisioning; never starts a service")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap")
    bootstrap.add_argument("--directory", required=True)
    bootstrap.add_argument("--trust-domain", required=True)
    bootstrap.add_argument("--issuer-server-name", required=True)
    issue = commands.add_parser("issue-node")
    for name in ("csr", "node-name", "node-uid", "node-ca", "node-ca-key", "trust-domain", "server-name", "output"):
        issue.add_argument(f"--{name}", required=True)
    arguments = parser.parse_args()
    if arguments.command == "bootstrap":
        bootstrap_pki(arguments.directory, arguments.trust_domain, arguments.issuer_server_name)
    else:
        for name in ("node_ca", "node_ca_key", "trust_domain", "server_name"):
            os.environ[f"ZOO_VM_CONTROL_{name.upper()}"] = getattr(arguments, name)
        csr = Path(arguments.csr).read_bytes()
        if len(csr) > 4096:
            raise ZooError("too_large", "Bounded P-256 node CSR required")
        certificate = issue_node(csr.decode(), arguments.node_name, arguments.node_uid)
        with os.fdopen(os.open(arguments.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
            handle.write(certificate)


if __name__ == "__main__":
    main()
