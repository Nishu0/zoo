"""Kubernetes without a cluster: the pod spec, kubeconfig access, the pieces of the API's protocol, the routing from
the configured runtime, and the deployment pieces that go with it (Postgres dialect, pod identity, readiness). The
driver against a real cluster is in tests/kubernetes."""

import json
import os
import stat
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from apps.server import docker, kms, kube, objects, runtime
from tests.conftest import ADMIN_EMAIL, signup


def test_quantities():
    assert kube.quantity("2Gi") == 2 << 30
    assert kube.quantity("500m") == 0.5
    assert kube.quantity("1e3") == 1000
    assert kube.quantity("128974848") == 128974848
    assert kube.quantity(None) == 0
    with pytest.raises(ValueError):
        kube.quantity("2Zi")


def test_exec_sets_user_environment_and_workdir():
    assert kube.user_argv(["ls"], None, None, None) == ["ls"]
    assert kube.user_argv(["ls"], "root", None, None) == ["ls"]
    argv = kube.user_argv(["ls", "-la"], "zoo", {"A": "1"}, "/home/zoo")
    assert argv == [
        "sh",
        "-c",
        'cd "$1" && shift && exec "$@"',
        "sh",
        "/home/zoo",
        "runuser",
        "-u",
        "zoo",
        "--",
        "env",
        "A=1",
        "ls",
        "-la",
    ]


def test_exit_status():
    assert kube.exit_code({"status": "Success"}, "p") == 0
    failure = {
        "status": "Failure",
        "reason": "NonZeroExitCode",
        "details": {"causes": [{"reason": "ExitCode", "message": "3"}]},
    }
    assert kube.exit_code(failure, "p") == 3
    with pytest.raises(kube.KubeError, match="container not found"):
        kube.exit_code({"status": "Failure", "message": "container not found"}, "p")
    with pytest.raises(kube.KubeError, match="without an exit status"):
        kube.exit_code(None, "p")


def test_why_a_pod_waits():
    unscheduled = {
        "status": {
            "conditions": [
                {"type": "PodScheduled", "status": "False", "reason": "Unschedulable", "message": "0/3 nodes"}
            ]
        }
    }
    assert kube.pod_problem(unscheduled) == "Unschedulable: 0/3 nodes"
    pulling = {
        "status": {
            "containerStatuses": [
                {"name": "sandbox", "state": {"waiting": {"reason": "ImagePullBackOff", "message": "not found"}}}
            ]
        }
    }
    assert kube.pod_problem(pulling) == "ImagePullBackOff: not found"
    creating = {
        "status": {"containerStatuses": [{"name": "sandbox", "state": {"waiting": {"reason": "ContainerCreating"}}}]}
    }
    assert kube.pod_problem(creating) == ""
    crashed = {
        "status": {
            "initContainerStatuses": [{"name": "home", "state": {"terminated": {"exitCode": 1, "reason": "Error"}}}]
        }
    }
    assert kube.pod_problem(crashed) == "home exited with 1: Error"


def test_a_sandbox_pod(monkeypatch):
    monkeypatch.setattr(
        kube, "home_config", lambda sandbox_id: {"home_claim": "configured-home", "node_name": "node-a"}
    )
    monkeypatch.setattr(kube, "PULL_SECRET", "regcred")
    monkeypatch.setattr(kube, "IDLE_MEMORY_MB_DESKTOP", 700)
    monkeypatch.setattr(kube, "IDLE_MEMORY_MB_CODE", 250)
    bound = Mock(return_value={"status": {"phase": "Bound"}})
    monkeypatch.setattr(kube, "bound_claim", bound)
    pod = kube.sandbox_pod("zoo-sandbox-sb1", "img:1", "sb1", {"ZOO_GUEST_TOKEN": "t"}, desktop=True)
    spec = pod["spec"]
    assert pod["metadata"]["labels"]["app.kubernetes.io/component"] == "sandbox"
    assert pod["metadata"]["labels"]["zoo.dev/sandbox"] == "sb1"
    assert spec["runtimeClassName"] == "kata" and spec["imagePullSecrets"] == [{"name": "regcred"}]
    assert spec["automountServiceAccountToken"] is False and spec["restartPolicy"] == "Never"
    [container] = spec["containers"]
    assert container["env"] == [{"name": "ZOO_GUEST_TOKEN", "value": "t"}]
    assert container["resources"]["limits"] == {"cpu": "2", "memory": "2Gi"}
    assert container["securityContext"]["capabilities"] == {"drop": ["NET_RAW"]}
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    # nothing listens for the API: the guest dials the gateway
    assert "ports" not in container
    assert {"name": "home", "persistentVolumeClaim": {"claimName": "configured-home"}} in spec["volumes"]
    assert spec["nodeName"] == "node-a" and spec["priorityClassName"] == "zoo-sandbox"
    assert "affinity" not in spec
    assert container["resources"]["requests"] == {"cpu": "100m", "memory": "700Mi"}
    # a new home claim gets the image's /home/zoo, as a new Docker volume does
    assert "cp -a /home/zoo/. /seed/" in spec["initContainers"][0]["command"][2]
    code = kube.sandbox_pod("zoo-sandbox-sb2", "img:1", "sb2", {}, desktop=False)
    assert "ports" not in code["spec"]["containers"][0]
    assert code["spec"]["containers"][0]["resources"]["requests"] == {"cpu": "100m", "memory": "250Mi"}
    assert bound.call_count == 2
    bound.assert_called_with(kube.NAMESPACE, "configured-home", "node-a")


def test_kubeconfig_with_an_exec_plugin(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin.py"
    plugin.write_text(
        "import json, os\n"
        "assert json.loads(os.environ['KUBERNETES_EXEC_INFO'])['kind'] == 'ExecCredential'\n"
        "print(json.dumps({'status': {'token': os.environ['TOKEN'], 'expirationTimestamp': '2999-01-01T00:00:00Z'}}))\n"
    )
    config = {
        "current-context": "dev",
        "contexts": [{"name": "dev", "context": {"cluster": "c", "user": "u"}}],
        "clusters": [
            {"name": "c", "cluster": {"server": "https://k8s.example:6443", "insecure-skip-tls-verify": True}}
        ],
        "users": [
            {
                "name": "u",
                "user": {
                    "exec": {
                        "apiVersion": "client.authentication.k8s.io/v1",
                        "command": sys.executable,
                        "args": [str(plugin)],
                        "env": [{"name": "TOKEN", "value": "from-plugin"}],
                    }
                },
            }
        ],
    }
    path = tmp_path / "config"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("KUBECONFIG", str(path))
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    api = kube.Api()
    api.load()
    assert api.server == "https://k8s.example:6443" and not api.in_cluster
    assert api.headers() == {"Authorization": "Bearer from-plugin"}
    # kept until it expires
    monkeypatch.setenv("TOKEN", "unused")
    assert api.headers() == {"Authorization": "Bearer from-plugin"}


def test_kubeconfig_credentials_from_data_are_private_files(tmp_path):
    path = kube.file_of({"client-key-data": "c2VjcmV0"}, "client-key", str(tmp_path))
    assert path is not None
    with open(path, "rb") as f:
        assert f.read() == b"secret"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    os.unlink(path)


@pytest.mark.parametrize("name,backend", [("docker", docker), ("kube", kube)])
def test_runtime_routes_linux_to_the_selected_backend(monkeypatch, name, backend):
    monkeypatch.setattr(runtime, "NAME", name)
    monkeypatch.setattr(runtime, "LINUX", backend)
    found = SimpleNamespace(name="pod")
    monkeypatch.setattr(backend, "container", lambda rid: found)
    assert runtime.backend("linux-sandbox") is backend
    assert runtime.container("linux-sandbox") is found
    assert runtime.is_kube() == (name == "kube")
    monkeypatch.setattr(kube, "NAMESPACE", "another-namespace")
    assert runtime.backend("linux-sandbox") is backend
    assert runtime.csi(None) == (name == "kube")
    assert not runtime.csi(SimpleNamespace(id="s"))


def test_missing_kubernetes_objects_use_native_errors():
    error = kube.NotFound(404, 'pods "x" not found')
    assert isinstance(error, kube.KubeError) and str(error) == 'pods "x" not found'
    assert isinstance(error, runtime.NOT_FOUND_ERRORS)


def test_aws_credentials_from_a_pod_identity(tmp_path, monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(kms, "_aws_credentials", {})
    with pytest.raises(RuntimeError, match="pod identity"):
        kms.aws_credentials()

    token = tmp_path / "token"
    token.write_text("web-identity-token\n")
    monkeypatch.setenv("AWS_ROLE_ARN", "arn:aws:iam::1:role/zoo")
    monkeypatch.setenv("AWS_WEB_IDENTITY_TOKEN_FILE", str(token))
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    sent = []

    def post(url, data, timeout):
        sent.append((url, data))
        xml = (
            '<AssumeRoleWithWebIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><AssumeRoleWithWebIdentityResult>'
            "<Credentials><AccessKeyId>AK</AccessKeyId><SecretAccessKey>SK</SecretAccessKey><SessionToken>ST</SessionToken>"
            "<Expiration>2999-01-01T00:00:00Z</Expiration></Credentials></AssumeRoleWithWebIdentityResult>"
            "</AssumeRoleWithWebIdentityResponse>"
        )
        return httpx.Response(200, text=xml)

    monkeypatch.setattr(kms.httpx, "post", post)
    assert kms.aws_credentials() == ("AK", "SK", "ST")
    assert sent[0][0] == "https://sts.eu-west-1.amazonaws.com/"
    assert sent[0][1]["WebIdentityToken"] == "web-identity-token" and sent[0][1]["RoleArn"] == "arn:aws:iam::1:role/zoo"
    # cached: STS isn't asked again
    assert kms.aws_credentials() == ("AK", "SK", "ST") and len(sent) == 1

    # presigned URLs carry the session token
    monkeypatch.setenv("ZOO_OBJECT_STORE", "s3://bucket/zoo")
    url = objects.presign("GET", "moves/x")
    assert "X-Amz-Security-Token=ST" in url and "X-Amz-Credential=AK%2F" in url


def test_aws_credentials_from_eks_pod_identity(tmp_path, monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(kms, "_aws_credentials", {})
    token = tmp_path / "token"
    token.write_text("pod-token")
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", "http://169.254.170.23/v1/credentials")
    monkeypatch.setenv("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE", str(token))

    def get(url, headers, timeout):
        assert headers == {"Authorization": "pod-token"}
        body = {"AccessKeyId": "AK2", "SecretAccessKey": "SK2", "Token": "T2", "Expiration": "2999-01-01T00:00:00Z"}
        return httpx.Response(200, json=body)

    monkeypatch.setattr(kms.httpx, "get", get)
    assert kms.aws_credentials() == ("AK2", "SK2", "T2")


def test_readiness_can_leave_remote_servers_out(client, alice, monkeypatch, runtime_requirements):
    from apps.server import health
    from tests.test_sandboxes import add_server

    checked = []
    monkeypatch.setattr(health, "host_requirements", lambda server: checked.append(server.id))
    add_server("alice@example.com", "macos")
    monkeypatch.setenv("ZOO_API_URL", "http://zoo.test")
    res = client.get("/readyz?servers=false")
    assert res.status_code == 200
    assert set(res.json()["checks"]) == runtime_requirements | {
        "database",
        "object_store",
        "jwt_secret",
        "secrets_key",
        "api_url",
        "gateway",
    }
    assert not checked


def test_the_kubernetes_status(client, alice, monkeypatch):
    assert client.get("/kubernetes", headers=alice).json() == {
        "enabled": False,
        "namespace": "",
        "runtime_class": "",
        "checks": [],
    }
    monkeypatch.setattr(runtime, "NAME", "kube")
    monkeypatch.setattr(runtime, "LINUX", kube)
    monkeypatch.setattr(kube, "NAMESPACE", "zoo-sandboxes")
    asked = []

    def check(probe=False):
        asked.append(probe)
        return [{"name": "runtime class", "ok": True, "detail": "kata (handler kata)"}]

    monkeypatch.setattr(kube, "check", check)
    from apps.server import servers_api

    monkeypatch.setattr(servers_api, "_kube_checked", {})
    status = client.get("/kubernetes", headers=alice).json()
    assert status["enabled"] and status["namespace"] == "zoo-sandboxes" and status["checks"][0]["ok"]
    client.get("/kubernetes", headers=alice)
    assert asked == [False]
    assert client.post("/kubernetes/check", headers=alice).status_code == 403
    admin = signup(client, ADMIN_EMAIL)
    assert client.post("/kubernetes/check", headers=admin).status_code == 200
    assert asked == [False, True]
