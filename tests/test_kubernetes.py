"""Kubernetes without a cluster: the pod spec, kubeconfig access, the pieces of the API's protocol, the routing from
the Docker backend, and the deployment pieces that go with it (Postgres dialect, pod identity, readiness). The
driver against a real cluster is in tests/kubernetes."""

import json
import os
import stat
import sys
from types import SimpleNamespace

import httpx
import pytest

from server import docker, kms, kube, objects
from tests.conftest import ADMIN_EMAIL, signup


def test_quantities():
    assert kube.quantity("2Gi") == 2 << 30
    assert kube.quantity("500m") == 0.5
    assert kube.quantity("1e3") == 1000
    assert kube.quantity("128974848") == 128974848
    assert kube.quantity(None) == 0
    with pytest.raises(ValueError):
        kube.quantity("2Zi")


def test_exec_runs_as_docker_exec_would():
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
    monkeypatch.setattr(kube, "RUNTIME_CLASS", "kata")
    monkeypatch.setattr(kube, "PULL_SECRET", "regcred")
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
    assert container["ports"] == [{"name": "vnc", "containerPort": 6080}]
    assert {"name": "home", "persistentVolumeClaim": {"claimName": "zoo-home-sb1"}} in spec["volumes"]
    # a new home claim gets the image's /home/zoo, as a new Docker volume does
    assert "cp -a /home/zoo/. /seed/" in spec["initContainers"][0]["command"][2]
    code = kube.sandbox_pod("zoo-sandbox-sb2", "img:1", "sb2", {}, desktop=False)
    assert "ports" not in code["spec"]["containers"][0]


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


def test_the_docker_backend_hands_this_machine_to_the_cluster(monkeypatch):
    assert docker.client_for(None) is docker.docker_client
    monkeypatch.setattr(kube, "NAMESPACE", "zoo-sandboxes")
    assert docker.client_for(None) is kube.cluster
    found = SimpleNamespace(name="pod")
    monkeypatch.setattr(kube, "container", lambda rid: found if rid == "k8s:zoo-sandboxes/p" else None)
    assert docker.container("k8s:zoo-sandboxes/p") is found
    # images are the kubelets' business
    docker.ensure_image(kube.cluster, "anything:1")
    assert docker.csi(None) is False
    monkeypatch.setattr(kube, "SNAPSHOT_CLASS", "csi-snapclass")
    assert docker.csi(None) and not docker.csi(SimpleNamespace(id="s"))


def test_missing_objects_are_dockers_not_found_too():
    import docker.errors

    error = kube.NotFound(404, 'pods "x" not found')
    assert isinstance(error, docker.errors.NotFound) and str(error) == 'pods "x" not found'


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


def test_readiness_can_leave_remote_servers_out(client, alice, monkeypatch):
    from server import health

    pinged = []
    monkeypatch.setattr(health, "ping", lambda server: pinged.append(server.id))
    client.post(
        "/servers", json={"name": "box", "docker_url": "ssh://zoo@box", "bind_address": "10.0.0.2"}, headers=alice
    )
    monkeypatch.setattr(docker.docker_client, "ping", lambda: True, raising=False)
    res = client.get("/readyz?servers=false")
    assert set(res.json()["checks"]) == {"database", "object_store", "jwt_secret", "secrets_key", "api_url", "docker"}
    assert not pinged


def test_the_kubernetes_status(client, alice, monkeypatch):
    assert client.get("/kubernetes", headers=alice).json() == {
        "enabled": False,
        "namespace": "",
        "runtime_class": "",
        "checks": [],
    }
    monkeypatch.setattr(kube, "NAMESPACE", "zoo-sandboxes")
    asked = []

    def check(probe=False):
        asked.append(probe)
        return [{"name": "runtime class", "ok": True, "detail": "kata (handler kata)"}]

    monkeypatch.setattr(kube, "check", check)
    from server import servers_api

    monkeypatch.setattr(servers_api, "_kube_checked", {})
    status = client.get("/kubernetes", headers=alice).json()
    assert status["enabled"] and status["namespace"] == "zoo-sandboxes" and status["checks"][0]["ok"]
    client.get("/kubernetes", headers=alice)
    assert asked == [False]
    assert client.post("/kubernetes/check", headers=alice).status_code == 403
    admin = signup(client, ADMIN_EMAIL)
    assert client.post("/kubernetes/check", headers=admin).status_code == 200
    assert asked == [False, True]
