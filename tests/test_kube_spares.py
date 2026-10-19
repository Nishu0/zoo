import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.server import kube, pool, runtime
from apps.server.errors import ZooError
from apps.server.sizes import Size
from db.connection import db_manager


def spare_claim(name="zoo-spare-home-a", node="node-a") -> dict:
    return {
        "metadata": {
            "name": name,
            "resourceVersion": "1",
            "labels": {
                **kube.LABELS,
                "app.kubernetes.io/component": "spare-home",
                kube.SPARE_NODE: kube.spare_node_label(node),
            },
            "annotations": {kube.SPARE_NODE: node},
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": "zoo-local",
            "resources": {"requests": {"storage": "10Gi"}},
        },
        "status": {"phase": "Pending"},
    }


@pytest.fixture
def spare_backend(monkeypatch):
    state = SimpleNamespace(
        owner=SimpleNamespace(
            id="candidate",
            runtime_id=None,
            config=json.dumps(
                {
                    "home_claim": "zoo-home-candidate",
                    "node_name": "node-a",
                    "node_reservation": {"cpu": 100, "memory": 250 << 20},
                }
            ),
        ),
        kind="sandbox",
        claims={"zoo-spare-home-a": spare_claim()},
        mounted=set(),
        events=[],
        transactions=0,
        cas_failed=False,
    )

    def persist(*, spare_claim, sandbox_id, home_claim, node_name):
        assert state.transactions == 1
        assert sandbox_id == state.owner.id
        config = json.loads(state.owner.config)
        assert config["home_claim"] == home_claim and config["node_name"] == node_name
        if state.cas_failed:
            return None
        state.owner.config = json.dumps({**config, "home_claim": spare_claim})
        state.events.append("pointer")
        return state.owner

    def delete_pool(*, id):
        assert state.kind == "pool"
        deleted = state.owner
        if deleted is not None:
            assert deleted.id == id
            state.owner = None
        return deleted

    repository = SimpleNamespace(
        lock_kube_placement=Mock(),
        get_sandbox_for_update=Mock(side_effect=lambda **kwargs: state.owner if state.kind == "sandbox" else None),
        get_pool_sandbox_for_update=Mock(side_effect=lambda **kwargs: state.owner if state.kind == "pool" else None),
        list_kube_home_claims=Mock(side_effect=lambda: iter([state.owner] if state.owner is not None else [])),
        claim_sandbox_spare_home=Mock(side_effect=persist),
        claim_pool_spare_home=Mock(side_effect=persist),
        delete_pool_sandbox=Mock(side_effect=delete_pool),
    )

    @contextmanager
    def session():
        state.transactions += 1
        try:
            yield repository
        finally:
            state.transactions -= 1
        state.events.append("commit")

    def get(path, **query):
        if path == "/apis/storage.k8s.io/v1/storageclasses/zoo-local":
            return {"volumeBindingMode": "WaitForFirstConsumer"}
        if path == kube.pods_path(kube.NAMESPACE):
            return {
                "items": [
                    {"spec": {"volumes": [{"persistentVolumeClaim": {"claimName": name}}]}} for name in state.mounted
                ]
            }
        if path == kube.claims_path(kube.NAMESPACE):
            assert query["labelSelector"] == f"app.kubernetes.io/managed-by=zoo,{kube.SPARE_NODE}"
            return {"items": copy.deepcopy(list(state.claims.values()))}
        if path.startswith(kube.claims_path(kube.NAMESPACE) + "/"):
            return copy.deepcopy(state.claims[path.rsplit("/", 1)[-1]])
        raise AssertionError(f"unexpected Kubernetes read: {path}")

    def request(method, path, body):
        assert method == "PATCH"
        name = path.rsplit("/", 1)[-1]
        claim = state.claims[name]
        assert body["metadata"]["resourceVersion"] == claim["metadata"]["resourceVersion"]
        assert json.loads(state.owner.config)["home_claim"] == name
        metadata = body["metadata"]
        if "labels" in metadata:
            claim["metadata"]["labels"].update(metadata["labels"])
            state.events.append("ownership")
        else:
            assert state.transactions == 0
            assert metadata["annotations"]["volume.kubernetes.io/selected-node"] == "node-a"
            claim["metadata"]["annotations"].update(metadata["annotations"])
            state.events.append("selected-node")
        return copy.deepcopy(claim)

    state.db = repository
    state.request = Mock(side_effect=request)
    state.create = Mock(side_effect=AssertionError("an available spare must not be recreated"))
    monkeypatch.setattr(kube, "HOME_SIZE", "10Gi")
    monkeypatch.setattr(kube, "STORAGE_CLASS", "zoo-local")
    monkeypatch.setattr(kube, "SPARE_VOLUMES", 1)
    monkeypatch.setattr(db_manager, "session", session)
    monkeypatch.setattr(kube.api, "get", get)
    monkeypatch.setattr(kube.api, "exists", lambda path: copy.deepcopy(state.claims.get(path.rsplit("/", 1)[-1])))
    monkeypatch.setattr(kube.api, "request", state.request)
    monkeypatch.setattr(kube, "create_spare_claim", state.create)
    monkeypatch.setattr(kube, "nodes_for_sandboxes", lambda: [{"metadata": {"name": "node-a"}}])
    return state


def test_available_spare_accepts_only_an_unassigned_pending_home(spare_backend):
    assert kube.available_spare(spare_claim(), "node-a", {}, set())


@pytest.mark.parametrize(
    "path,value",
    [
        (("reference",), "candidate"),
        (("mounted",), True),
        (("metadata", "labels", "zoo.dev/sandbox"), "another-sandbox"),
        (("metadata", "deletionTimestamp"), "2026-10-13T00:00:00Z"),
        (("status", "phase"), "Bound"),
        (("spec", "volumeName"), "home-volume"),
        (("metadata", "annotations", "volume.kubernetes.io/selected-node"), "node-a"),
        (("metadata", "labels", kube.SPARE_NODE), kube.spare_node_label("node-b")),
        (("metadata", "annotations", kube.SPARE_NODE), "node-b"),
        (("spec", "storageClassName"), "wrong-storage"),
        (("spec", "resources", "requests", "storage"), "5Gi"),
        (("spec", "accessModes"), ["ReadWriteMany"]),
        (("spec", "volumeMode"), "Block"),
        (("spec", "dataSource"), {"kind": "VolumeSnapshot", "name": "snapshot"}),
        (("spec", "dataSourceRef"), {"kind": "PersistentVolumeClaim", "name": "other-home"}),
        (("metadata", "labels", "app.kubernetes.io/component"), "home"),
    ],
)
def test_available_spare_rejects_referenced_or_incompatible_claims(spare_backend, path, value):
    claim = spare_claim()
    references = {}
    mounted = set()
    if path == ("reference",):
        references[claim["metadata"]["name"]] = value
    elif path == ("mounted",):
        mounted.add(claim["metadata"]["name"])
    else:
        target = claim
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    assert not kube.available_spare(claim, "node-a", references, mounted)


@pytest.mark.parametrize("kind", ["sandbox", "pool"])
def test_prepare_commits_the_spare_pointer_before_selected_node_provisioning(spare_backend, kind):
    spare_backend.kind = kind
    name = kube.prepare_home_claim("candidate", "node-a", Size())
    assert name == "zoo-spare-home-a"
    assert json.loads(spare_backend.owner.config)["home_claim"] == name
    assert "volume.kubernetes.io/selected-node" not in spare_backend.claims[name]["metadata"]["annotations"]
    kube.ensure_claim(kube.NAMESPACE, name, storage="10Gi", node="node-a")
    assert spare_backend.events == ["pointer", "commit", "ownership", "selected-node"]
    parameters = {
        "spare_claim": name,
        "sandbox_id": "candidate",
        "home_claim": "zoo-home-candidate",
        "node_name": "node-a",
    }
    selected = (
        spare_backend.db.claim_sandbox_spare_home if kind == "sandbox" else spare_backend.db.claim_pool_spare_home
    )
    unselected = (
        spare_backend.db.claim_pool_spare_home if kind == "sandbox" else spare_backend.db.claim_sandbox_spare_home
    )
    selected.assert_called_once_with(**parameters)
    unselected.assert_not_called()
    spare_backend.create.assert_not_called()


def test_failed_pointer_compare_and_swap_does_not_assign_or_provision_the_spare(spare_backend):
    spare_backend.cas_failed = True
    with pytest.raises(ZooError) as error:
        kube.prepare_home_claim("candidate", "node-a", Size())
    assert error.value.code == "conflict"
    assert json.loads(spare_backend.owner.config)["home_claim"] == "zoo-home-candidate"
    spare_backend.request.assert_not_called()
    assert (
        "volume.kubernetes.io/selected-node" not in spare_backend.claims["zoo-spare-home-a"]["metadata"]["annotations"]
    )


@pytest.mark.parametrize("mounted", [False, True])
def test_refill_recovers_pointer_owned_but_unlabeled_claim_without_deleting_it(spare_backend, monkeypatch, mounted):
    config = json.loads(spare_backend.owner.config)
    spare_backend.owner.config = json.dumps({**config, "home_claim": "zoo-spare-home-a"})
    spare_backend.claims["zoo-spare-home-b"] = spare_claim("zoo-spare-home-b")
    if mounted:
        spare_backend.mounted.add("zoo-spare-home-a")
    delete = Mock(side_effect=AssertionError("owned and available spare claims must not be deleted"))
    monkeypatch.setattr(kube, "delete_claim", delete)
    assert kube.refill_spare_volumes() == {"node-a": 1}
    labels = spare_backend.claims["zoo-spare-home-a"]["metadata"]["labels"]
    assert labels["zoo.dev/sandbox"] == "candidate"
    assert labels["app.kubernetes.io/component"] == "home"
    assert json.loads(spare_backend.owner.config)["home_claim"] == "zoo-spare-home-a"
    assert spare_backend.events == ["ownership", "commit"]
    delete.assert_not_called()
    spare_backend.create.assert_not_called()


@pytest.mark.parametrize("previous", [None, "zoo-previous-home-a"])
def test_remove_home_uses_saved_pool_config_after_row_deletion(spare_backend, monkeypatch, previous):
    spare_backend.kind = "pool"
    config = {"home_claim": "zoo-spare-home-a", "node_name": "node-a"}
    if previous is not None:
        config["previous_home_claim"] = previous
    spare_backend.owner.config = json.dumps(config)
    row = spare_backend.owner
    remove_container = Mock()
    lookup = Mock(side_effect=AssertionError("deleted pool rows cannot supply cleanup config"))
    deleted = []

    def delete_claim(namespace, name, *, wait):
        assert spare_backend.owner is None and spare_backend.transactions == 0
        deleted.append((namespace, name, wait))

    monkeypatch.setattr(runtime, "NAME", "kube")
    monkeypatch.setattr(runtime, "LINUX", kube)
    monkeypatch.setattr(runtime, "remove_named_container", remove_container)
    monkeypatch.setattr(kube, "home_config", lookup)
    monkeypatch.setattr(kube, "delete_claim", delete_claim)
    manager = pool.Pool()
    manager.discard(row, None)
    expected = [previous, config["home_claim"]] if previous is not None else [config["home_claim"]]
    assert deleted == [(kube.NAMESPACE, name, True) for name in expected]
    remove_container.assert_called_once_with("zoo-sandbox-candidate")
    lookup.assert_not_called()
    assert config["home_claim"] == "zoo-spare-home-a"
