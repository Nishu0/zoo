import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from db.connection import db_manager
from db.generated.query import CreatePoolSandboxParams, Querier
from server import kube
from server.errors import ZooError
from server.sizes import Size
from tests.conftest import present


def node(name, memory="2Gi", cpu="2") -> dict:
    return {
        "metadata": {"name": name, "labels": {"kubernetes.io/hostname": name}},
        "spec": {},
        "status": {"allocatable": {"cpu": cpu, "memory": memory}},
    }


def pool_home(sandbox_id, node_name=None):
    config = {"home_claim": f"zoo-home-{sandbox_id}"}
    if node_name is not None:
        config["node_name"] = node_name
    with db_manager.session() as db:
        return present(
            db.create_pool_sandbox(
                CreatePoolSandboxParams(
                    id=sandbox_id, kind="code", server_id=None, image="img:1", config=json.dumps(config)
                )
            )
        )


def pool_config(sandbox_id) -> dict:
    with db_manager.session() as db:
        return json.loads(present(db.get_pool_sandbox_for_update(id=sandbox_id)).config)


def pod(node_name, memory, cpu="100m", sandbox_id=None, namespace="workload", phase="Running") -> dict:
    return {
        "metadata": {"namespace": namespace, "labels": {"zoo.dev/sandbox": sandbox_id} if sandbox_id else {}},
        "spec": {"nodeName": node_name, "containers": [{"resources": {"requests": {"cpu": cpu, "memory": memory}}}]},
        "status": {"phase": phase},
    }


def volume_on(node_name) -> dict:
    return {
        "spec": {
            "nodeAffinity": {
                "required": {
                    "nodeSelectorTerms": [
                        {
                            "matchExpressions": [
                                {"key": "kubernetes.io/hostname", "operator": "In", "values": [node_name]}
                            ]
                        }
                    ]
                }
            }
        }
    }


@pytest.fixture
def cluster(monkeypatch):
    state = SimpleNamespace(
        nodes=[node("node-a"), node("node-b")],
        pods=[],
        claims={},
        volumes={},
        overhead={},
        load=Mock(),
        request=Mock(side_effect=AssertionError("unexpected Kubernetes mutation")),
    )

    def get(path, **query):
        if path == "/apis/node.k8s.io/v1/runtimeclasses/kata":
            return {"overhead": {"podFixed": state.overhead}}
        if path == "/api/v1/pods":
            assert query["fieldSelector"] == "status.phase!=Succeeded,status.phase!=Failed"
            return {
                "items": copy.deepcopy(
                    [item for item in state.pods if item["status"]["phase"] not in {"Succeeded", "Failed"}]
                )
            }
        if path.startswith("/api/v1/persistentvolumes/"):
            return copy.deepcopy(state.volumes[path.rsplit("/", 1)[-1]])
        if path.startswith("/api/v1/nodes/"):
            return copy.deepcopy(
                next(item for item in state.nodes if item["metadata"]["name"] == path.rsplit("/", 1)[-1])
            )
        if path.startswith(kube.claims_path(kube.NAMESPACE) + "/"):
            return copy.deepcopy(state.claims[path.rsplit("/", 1)[-1]])
        raise AssertionError(f"unexpected Kubernetes read: {path}")

    state.get = get
    monkeypatch.setattr(kube, "IDLE_MEMORY_MB_CODE", 250)
    monkeypatch.setattr(kube, "IDLE_MEMORY_MB_DESKTOP", 700)
    monkeypatch.setattr(kube, "STORAGE_CLASS", "zoo-local")
    monkeypatch.setattr(kube, "HOME_SIZE", "10Gi")
    monkeypatch.setattr(kube, "nodes_for_sandboxes", lambda: copy.deepcopy(state.nodes))
    monkeypatch.setattr(kube.api, "get", get)
    monkeypatch.setattr(kube.api, "exists", lambda path: copy.deepcopy(state.claims.get(path.rsplit("/", 1)[-1])))
    monkeypatch.setattr(kube.api, "load", state.load)
    monkeypatch.setattr(kube.api, "request", state.request)
    return state


def test_placement_uses_allocatable_minus_active_requests_instead_of_limits(cluster):
    pool_home("candidate")
    cluster.nodes = [node("node-a", "4Gi", "4"), node("node-b", "2Gi", "2")]
    cluster.pods = [pod("node-a", "3Gi", "3"), pod("node-b", "128Mi"), pod("node-b", "2Gi", phase="Succeeded")]
    cluster.pods[1]["spec"]["containers"][0]["resources"]["limits"] = {"cpu": "2", "memory": "2Gi"}
    assert kube.reserve_node("candidate", False, Size()) == "node-b"
    assert pool_config("candidate")["node_reservation"] == {"cpu": 100, "memory": 250 << 20}


def test_placement_counts_init_sidecars_and_runtime_overhead(cluster):
    pool_home("candidate")
    cluster.nodes = [node("node-a", "800Mi"), node("node-b", "450Mi")]
    workload = pod("node-a", "100Mi", "200m")
    workload["spec"].update(
        initContainers=[
            {"restartPolicy": "Always", "resources": {"requests": {"cpu": "100m", "memory": "50Mi"}}},
            {"resources": {"requests": {"cpu": "400m", "memory": "300Mi"}}},
        ],
        overhead={"cpu": "50m", "memory": "25Mi"},
    )
    assert kube.pod_requests(workload) == {"cpu": 550, "memory": 375 << 20}
    cluster.pods = [workload]
    cluster.overhead = {"cpu": "25m", "memory": "64Mi"}
    assert kube.reserve_node("candidate", False, Size()) == "node-b"
    assert pool_config("candidate")["node_reservation"] == {"cpu": 125, "memory": 314 << 20}


def test_restarts_keep_the_persisted_node_even_when_another_node_has_more_room(cluster):
    pool_home("restart", node_name="node-a")
    cluster.nodes = [node("node-a", "500Mi"), node("node-b", "16Gi")]
    assert kube.reserve_node("restart", False, Size()) == "node-a"
    kube.release_node_reservation("restart", "node-a")
    assert "node_reservation" not in pool_config("restart")
    assert kube.reserve_node("restart", False, Size()) == "node-a"
    assert pool_config("restart")["home_claim"] == "zoo-home-restart"


def test_restarts_reject_a_full_persisted_node_instead_of_moving_the_home(cluster):
    pool_home("restart", node_name="node-a")
    cluster.nodes = [node("node-a", "200Mi"), node("node-b", "16Gi")]
    with pytest.raises(ZooError) as error:
        kube.reserve_node("restart", False, Size())
    assert error.value.code == "unavailable"
    assert pool_config("restart")["node_name"] == "node-a"
    assert "node_reservation" not in pool_config("restart")


@pytest.mark.parametrize("affinity_key", ["matchExpressions", "matchFields"])
def test_bound_volume_affinity_constrains_node_selection(cluster, affinity_key):
    pool_home("bound")
    cluster.nodes = [node("node-a", "500Mi"), node("node-b", "16Gi")]
    cluster.claims["zoo-home-bound"] = {
        "metadata": {},
        "spec": {"volumeName": "home-volume"},
        "status": {"phase": "Bound"},
    }
    key = "kubernetes.io/hostname" if affinity_key == "matchExpressions" else "metadata.name"
    cluster.volumes["home-volume"] = {
        "spec": {
            "nodeAffinity": {
                "required": {
                    "nodeSelectorTerms": [{affinity_key: [{"key": key, "operator": "In", "values": ["node-a"]}]}]
                }
            }
        }
    }
    assert kube.reserve_node("bound", False, Size()) == "node-a"
    kube.bound_claim(kube.NAMESPACE, "zoo-home-bound", "node-a")
    with pytest.raises(ZooError) as error:
        kube.bound_claim(kube.NAMESPACE, "zoo-home-bound", "node-b")
    assert error.value.code == "conflict"


def test_persisted_node_cannot_override_the_claims_selected_node(cluster):
    pool_home("conflict", node_name="node-b")
    cluster.claims["zoo-home-conflict"] = {
        "metadata": {"annotations": {"volume.kubernetes.io/selected-node": "node-a"}},
        "spec": {},
        "status": {"phase": "Pending"},
    }
    with pytest.raises(ZooError) as error:
        kube.reserve_node("conflict", False, Size())
    assert error.value.code == "conflict"
    assert "node_reservation" not in pool_config("conflict")


def test_visible_pods_do_not_count_their_reservations_twice(cluster):
    pool_home("visible")
    pool_home("candidate")
    cluster.nodes = [node("node-a", "500Mi")]
    assert kube.reserve_node("visible", False, Size()) == "node-a"
    cluster.pods = [pod("node-a", "250Mi", sandbox_id="visible", namespace=kube.NAMESPACE)]
    assert kube.reserve_node("candidate", False, Size()) == "node-a"


@pytest.mark.parametrize(
    "desktop,size",
    [
        (False, Size(cpus=0.05)),
        (False, Size(memory_mb=249)),
        (True, Size(memory_mb=699)),
        (False, Size(cpus=float("nan"))),
        (False, Size(cpus=float("inf"))),
    ],
)
def test_undersized_limits_fail_before_kubernetes_io(cluster, desktop, size):
    with pytest.raises(ZooError) as error:
        kube.run_container("pod", "img:1", "candidate", {}, desktop=desktop, size=size)
    assert error.value.code == "bad_request"
    cluster.load.assert_not_called()
    cluster.request.assert_not_called()


def test_home_claim_binds_before_the_pod_is_assigned_a_node(cluster, monkeypatch):
    pool_home("boot")
    monkeypatch.setattr(kube, "existing_pod", lambda namespace, name: None)
    posted = []
    events = []

    def request(method, path, body):
        assert method == "POST"
        if path == kube.claims_path(kube.NAMESPACE):
            events.append("pending")
            claim = copy.deepcopy(body)
            claim["status"] = {"phase": "Pending"}
            cluster.claims[body["metadata"]["name"]] = claim
        else:
            assert path == kube.pods_path(kube.NAMESPACE)
            claim = cluster.claims["zoo-home-boot"]
            assert claim["status"]["phase"] == "Bound"
            assert body["spec"]["nodeName"] == claim["metadata"]["annotations"]["volume.kubernetes.io/selected-node"]
            events.append("pod")
            posted.append(copy.deepcopy(body))
        return {}

    def bind_claim(delay):
        claim = cluster.claims["zoo-home-boot"]
        events.append("bound")
        claim["status"]["phase"] = "Bound"
        claim["spec"]["volumeName"] = "home-volume"
        cluster.volumes["home-volume"] = volume_on(
            claim["metadata"]["annotations"]["volume.kubernetes.io/selected-node"]
        )

    monkeypatch.setattr(kube.api, "request", request)
    monkeypatch.setattr(kube, "time", SimpleNamespace(monotonic=lambda: 0, sleep=bind_claim))
    monkeypatch.setattr(kube, "wait_running", lambda namespace, name, timeout: posted[0])
    assert kube.run_container(kube.pod_name("boot"), "img:1", "boot", {}, desktop=False, size=Size(disk_gb=20)) == (
        kube.runtime_id(kube.NAMESPACE, kube.pod_name("boot")),
        None,
    )
    assert events == ["pending", "bound", "pod"]
    assert "node_reservation" not in pool_config("boot")


def test_binding_failure_releases_the_reservation_without_creating_a_pod(cluster, monkeypatch):
    pool_home("failed-boot")
    monkeypatch.setattr(kube, "existing_pod", lambda namespace, name: None)
    monkeypatch.setattr(kube, "ensure_claim", Mock())
    monkeypatch.setattr(kube, "wait_bound", Mock(side_effect=ZooError("timeout", "claim did not bind")))
    with pytest.raises(ZooError) as error:
        kube.run_container(
            kube.pod_name("failed-boot"), "img:1", "failed-boot", {}, desktop=False, size=Size(disk_gb=20)
        )
    assert error.value.code == "timeout"
    cluster.request.assert_not_called()
    assert "node_name" in pool_config("failed-boot")
    assert "node_reservation" not in pool_config("failed-boot")


def test_concurrent_reservations_cannot_claim_the_same_remaining_capacity(cluster, monkeypatch):
    pool_home("first")
    pool_home("second")
    cluster.nodes = [node("node-a", "250Mi", "100m")]
    first_listing = threading.Event()
    second_attempt = threading.Event()
    attempts = []
    counter_lock = threading.Lock()
    native_lock = Querier.lock_kube_placement

    def lock_placement(self, *, namespace):
        with counter_lock:
            attempts.append(namespace)
            if len(attempts) == 2:
                second_attempt.set()
        return native_lock(self, namespace=namespace)

    def get(path, **query):
        if path == "/api/v1/pods" and not first_listing.is_set():
            first_listing.set()
            assert second_attempt.wait(5), "second placement never reached the database lock"
        return cluster.get(path, **query)

    monkeypatch.setattr(Querier, "lock_kube_placement", lock_placement)
    monkeypatch.setattr(kube.api, "get", get)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(kube.reserve_node, "first", False, Size())
        assert first_listing.wait(5), "first placement never read active requests"
        second = executor.submit(kube.reserve_node, "second", False, Size())
        assert first.result(timeout=10) == "node-a"
        with pytest.raises(ZooError) as error:
            second.result(timeout=10)
    assert error.value.code == "unavailable"
    assert attempts == [kube.NAMESPACE, kube.NAMESPACE]
    assert pool_config("first")["node_reservation"] == {"cpu": 100, "memory": 250 << 20}
    assert "node_reservation" not in pool_config("second")
