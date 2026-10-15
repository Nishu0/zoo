import runpy
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from db.connection import db_manager
from server import docker, nodes, requirements, runtime
from server.errors import ZooError


@pytest.mark.parametrize(
    "name,expected",
    [
        ("docker", {"docker", "isolation", "sandbox_images"}),
        ("kube", {"kube", "kvm", "runtime_class", "storage_class", "snapshot_class"}),
    ],
)
def test_only_the_selected_runtimes_requirements_are_mandatory(monkeypatch, name, expected):
    monkeypatch.setattr(runtime, "NAME", name)
    assert requirements.__file__ is not None
    checks = runpy.run_path(requirements.__file__)["CHECKS"]
    assert set(checks) == expected | {
        "database",
        "object_store",
        "jwt_secret",
        "secrets_key",
        "api_url",
        "runtime",
        "gateway",
    }


@pytest.mark.parametrize("name", ["docker", "kube"])
def test_runtime_setting_accepts_supported_backends(monkeypatch, name):
    monkeypatch.setenv("ZOO_RUNTIME", name)
    requirements.runtime_setting()


@pytest.mark.parametrize("name", ["", "runc", "invalid"])
def test_runtime_setting_rejects_other_backends(monkeypatch, name):
    monkeypatch.setenv("ZOO_RUNTIME", name)
    with pytest.raises(RuntimeError, match="ZOO_RUNTIME must be docker or kube"):
        requirements.runtime_setting()


def test_isolation_setting_must_be_known(monkeypatch):
    monkeypatch.setattr(docker, "ISOLATION", "vm")
    with pytest.raises(RuntimeError, match="ZOO_ISOLATION must be auto, kata or runc"):
        requirements.isolation()


def test_kata_isolation_requires_the_kata_runtime(monkeypatch):
    monkeypatch.setattr(docker, "ISOLATION", "kata")
    monkeypatch.setattr(runtime, "local_info", lambda: {"Runtimes": {}})
    with pytest.raises(RuntimeError, match="Docker has no io.containerd.kata.v2 runtime"):
        requirements.isolation()
    monkeypatch.setattr(runtime, "local_info", lambda: {"Runtimes": {"io.containerd.kata.v2": {}}})
    requirements.isolation()


@pytest.mark.parametrize("setting", ["auto", "runc"])
def test_auto_and_runc_isolation_need_no_kata(monkeypatch, setting):
    monkeypatch.setattr(docker, "ISOLATION", setting)
    monkeypatch.setattr(runtime, "local_info", lambda: {"Runtimes": {}})
    requirements.isolation()


@pytest.mark.parametrize("runtimes,expected", [({}, "runc"), ({"io.containerd.kata.v2": {}}, "io.containerd.kata.v2")])
def test_auto_isolation_picks_kata_when_docker_has_it(monkeypatch, runtimes, expected):
    monkeypatch.setattr(docker, "ISOLATION", "auto")
    monkeypatch.setattr(docker, "_runtime", None)
    monkeypatch.setattr(docker, "info", lambda: {"Runtimes": runtimes})
    assert docker.container_runtime() == expected


@pytest.mark.parametrize("image", [runtime.IMAGE, runtime.CODE_IMAGE])
def test_docker_requires_each_sandbox_image(monkeypatch, image):
    def get(name):
        if name == image:
            raise docker.docker.errors.ImageNotFound("sandbox image missing")
        return object()

    monkeypatch.setattr(docker, "docker_client", SimpleNamespace(images=SimpleNamespace(get=get)))
    with pytest.raises(docker.docker.errors.ImageNotFound, match="sandbox image missing"):
        runtime.sandbox_images()


@pytest.mark.parametrize("name", ["kvm", "runtime class", "storage", "snapshots"])
def test_kubernetes_requires_every_cluster_dependency(monkeypatch, name):
    monkeypatch.setattr(runtime, "kubernetes_check", list)
    with pytest.raises(RuntimeError, match=f"Kubernetes did not report its {name} requirement"):
        requirements.cluster_requirement(name)
    monkeypatch.setattr(runtime, "kubernetes_check", lambda: [{"name": name, "ok": False, "detail": "missing"}])
    with pytest.raises(RuntimeError, match="missing"):
        requirements.cluster_requirement(name)
    monkeypatch.setattr(runtime, "kubernetes_check", lambda: [{"name": name, "ok": True, "detail": "ready"}])
    requirements.cluster_requirement(name)


@pytest.mark.parametrize("name", ["docker", "isolation", "sandbox_images"])
def test_readiness_fails_when_a_runtime_requirement_fails(client, monkeypatch, runtime_requirements, name):
    monkeypatch.setenv("ZOO_API_URL", "http://zoo.test")

    def unavailable():
        raise RuntimeError("required runtime dependency unavailable")

    monkeypatch.setitem(requirements.CHECKS, name, unavailable)
    response = client.get("/readyz?servers=false")
    assert response.status_code == 503
    assert response.json()["checks"][name] == "required runtime dependency unavailable"
    assert set(response.json()["checks"]) >= runtime_requirements


@pytest.fixture(params=[("macos", "arm64"), ("windows", "amd64")])
def vm_host(monkeypatch, request):
    platform, arch = request.param
    server = SimpleNamespace(id="host", name="vm-host", platform=platform, docker_url="node://zoo@host")
    state = SimpleNamespace(
        server=server,
        node=SimpleNamespace(arch=arch),
        connected=True,
        servers=[server],
        probe=Mock(return_value={"online": True, "microvm": True}),
        base=Mock(return_value={"ready": True}),
    )
    repository = SimpleNamespace(
        get_node_by_server=lambda server_id: state.node,
        list_all_servers=lambda: iter(state.servers),
    )

    @contextmanager
    def session():
        yield repository

    monkeypatch.setattr(db_manager, "session", session)
    monkeypatch.setattr(nodes.hub, "connected", lambda server_id: state.connected)
    monkeypatch.setattr(runtime.VMS[platform], "probe", state.probe)
    monkeypatch.setattr(runtime.VMS[platform], "base_status", state.base)
    return state


def test_registered_vm_host_requires_a_connected_node_and_ready_native_vm(vm_host):
    runtime.host_requirements(vm_host.server)
    vm_host.probe.assert_called_once_with(vm_host.server.id, vm_host.server.docker_url)
    vm_host.base.assert_called_once_with(vm_host.server.id)


@pytest.mark.parametrize("missing", ["registration", "connection"])
def test_vm_host_rejects_missing_or_disconnected_nodes(vm_host, missing):
    if missing == "registration":
        vm_host.node = None
    else:
        vm_host.connected = False
    with pytest.raises(ZooError) as error:
        runtime.host_requirements(vm_host.server)
    assert error.value.code == "unavailable" and "zoo-node is not connected" in error.value.detail
    vm_host.probe.assert_not_called()
    vm_host.base.assert_not_called()


@pytest.mark.parametrize("vm_host", [("macos", "amd64")], indirect=True)
def test_macos_hosts_require_apple_silicon(vm_host):
    with pytest.raises(ZooError) as error:
        runtime.host_requirements(vm_host.server)
    assert error.value.code == "not_supported" and "Apple Silicon" in error.value.detail
    vm_host.probe.assert_not_called()
    vm_host.base.assert_not_called()


@pytest.mark.parametrize(
    "report",
    [
        {"online": False, "microvm": True, "error": "native probe failed"},
        {"online": True, "microvm": False},
        {"online": True},
    ],
)
def test_vm_hosts_require_a_successful_native_vm_probe(vm_host, report):
    vm_host.probe.return_value = report
    with pytest.raises(ZooError) as error:
        runtime.host_requirements(vm_host.server)
    assert error.value.code == "unavailable"
    if "error" in report:
        assert error.value.detail == report["error"]
    vm_host.base.assert_not_called()


@pytest.mark.parametrize("status", [{"state": "running", "ready": False}, {"state": "installing"}])
def test_vm_hosts_require_a_ready_base(vm_host, status):
    vm_host.base.return_value = status
    with pytest.raises(ZooError) as error:
        runtime.host_requirements(vm_host.server)
    assert error.value.code == "unavailable" and "base VM is not ready" in error.value.detail


def test_readiness_reports_native_host_requirement_failures(client, monkeypatch, runtime_requirements, vm_host):
    monkeypatch.setenv("ZOO_API_URL", "http://zoo.test")
    response = client.get("/readyz")
    assert response.status_code == 200 and response.json()["checks"]["server:vm-host"] == "ok"
    vm_host.base.return_value = {"ready": False}
    response = client.get("/readyz")
    assert response.status_code == 503 and response.json()["status"] == "unavailable"
    assert "base VM is not ready" in response.json()["checks"]["server:vm-host"]


def test_readiness_can_exclude_unavailable_hosts(client, monkeypatch, runtime_requirements, vm_host):
    monkeypatch.setenv("ZOO_API_URL", "http://zoo.test")
    vm_host.connected = False
    response = client.get("/readyz?servers=false")
    assert response.status_code == 200 and "server:vm-host" not in response.json()["checks"]
    vm_host.probe.assert_not_called()
    vm_host.base.assert_not_called()


def test_startup_collects_named_failures_for_every_vm_host(monkeypatch, vm_host):
    other = SimpleNamespace(id="other", name="other-host", platform="windows", docker_url="node://zoo@other")
    vm_host.servers.append(other)
    monkeypatch.setattr(requirements, "CHECKS", {"runtime": lambda: None})
    failure = ZooError("unavailable", "host bootstrap incomplete")
    checker = Mock(side_effect=failure)
    monkeypatch.setattr(runtime, "host_requirements", checker)
    assert requirements.failures() == [f"server:vm-host: {failure}", f"server:other-host: {failure}"]
    assert [args.args[0].id for args in checker.call_args_list] == ["host", "other"]


def test_startup_reports_failure_to_list_registered_hosts(monkeypatch):
    monkeypatch.setattr(requirements, "CHECKS", {"runtime": lambda: None})
    monkeypatch.setattr(db_manager, "session", Mock(side_effect=ConnectionError("host inventory unavailable")))
    assert requirements.failures() == ["vm_hosts: host inventory unavailable"]


@pytest.mark.parametrize("dependency_error", [None, RuntimeError("dependency unavailable")])
def test_native_gateway_preflight_checks_hosts_and_own_dependencies(monkeypatch, dependency_error):
    database = Mock()
    selected_runtime = Mock(side_effect=dependency_error)
    api_url = Mock()
    registered = SimpleNamespace(name="registered-vm")

    @contextmanager
    def session():
        yield SimpleNamespace(list_all_servers=lambda: [registered])

    inventory = Mock(side_effect=session)
    probe = Mock()
    monkeypatch.setattr(requirements, "CHECKS", {"database": database, "runtime": selected_runtime, "api_url": api_url})
    monkeypatch.setattr(db_manager, "session", inventory)
    monkeypatch.setattr(runtime, "host_requirements", probe)

    expected = [] if dependency_error is None else ["runtime: dependency unavailable"]
    assert requirements.failures() == expected
    database.assert_called_once_with()
    selected_runtime.assert_called_once_with()
    api_url.assert_called_once_with()
    inventory.assert_called_once_with()
    probe.assert_called_once_with(registered)
