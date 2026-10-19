import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.server import kube, objects
from apps.server.errors import ZooError
from db.connection import db_manager
from db.generated.query import Querier
from tests.conftest import present


@pytest.fixture
def moving_home(sandbox, monkeypatch):
    sandbox_id = sandbox["id"]
    with db_manager.session() as db:
        stored = present(db.get_sandbox_for_update(id=sandbox_id))
        config = {**json.loads(stored.config), "node_name": "node-a"}
        db.update_sandbox(name=stored.name, resources=stored.resources, config=json.dumps(config), id=sandbox_id)
        db.clear_sandbox_runtime(id=sandbox_id)
        db.set_sandbox_stopped(id=sandbox_id)

    source = config["home_claim"]
    state = SimpleNamespace(
        sandbox_id=sandbox_id,
        source=source,
        claims={source: "node-a"},
        created=[],
        copied=[],
        deleted=[],
        snapshots=[],
        removed_snapshots=[],
    )

    def get_claim(path):
        name = path.rsplit("/", 1)[-1]
        if name not in state.claims:
            raise kube.NotFound(404, "claim not found")
        return {
            "metadata": {"annotations": {"volume.kubernetes.io/selected-node": state.claims[name]}},
            "spec": {"resources": {"requests": {"storage": "10Gi"}}},
        }

    def snapshot(sandbox_id, snapshot_id):
        assert sandbox_id == state.sandbox_id and state.source in state.claims
        state.snapshots.append(snapshot_id)
        return 4096

    def ensure_claim(namespace, name, source=None, storage="10Gi", node=""):
        assert namespace == kube.NAMESPACE and storage == "10Gi"
        state.claims[name] = node
        state.created.append(name)

    def restore_claim(sandbox_id, claim, snapshot_id, node=""):
        assert sandbox_id == state.sandbox_id and state.claims[claim] == node
        assert snapshot_id in state.snapshots
        state.copied.append(claim)

    def delete_claim(namespace, claim, wait=False):
        assert namespace == kube.NAMESPACE
        state.deleted.append(claim)
        state.claims.pop(claim, None)

    monkeypatch.setattr(kube, "nodes_for_sandboxes", lambda: [{"metadata": {"name": "node-b"}}])
    monkeypatch.setattr(kube, "existing_pod", lambda namespace, name: None)
    monkeypatch.setattr(kube.api, "get", get_claim)
    monkeypatch.setattr(kube, "snapshot", snapshot)
    monkeypatch.setattr(kube, "ensure_claim", ensure_claim)
    monkeypatch.setattr(kube, "restore_claim", restore_claim)
    monkeypatch.setattr(kube, "delete_claim", delete_claim)
    monkeypatch.setattr(kube, "remove_snapshot", state.removed_snapshots.append)
    return state


@pytest.mark.parametrize("failure", ["snapshot", "ensure_claim", "restore_claim"])
def test_move_failure_before_copy_commits_retains_the_source(moving_home, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise ZooError("upstream_error", "home copy failed")

    monkeypatch.setattr(kube, failure, fail)
    with pytest.raises(ZooError, match="home copy failed"):
        kube.move_volume(moving_home.sandbox_id, "node-b")

    config = kube.home_config(moving_home.sandbox_id)
    assert config["home_claim"] == moving_home.source
    assert config["node_name"] == "node-a"
    assert not config.get("previous_home_claim")
    assert moving_home.claims == {moving_home.source: "node-a"}
    assert moving_home.source not in moving_home.deleted
    assert moving_home.copied == []


def test_move_retry_finishes_source_cleanup_without_recopying(moving_home, monkeypatch):
    delete_claim = kube.delete_claim
    attempts = []

    def fail_first_source_cleanup(namespace, claim, wait=False):
        if claim == moving_home.source:
            config = kube.home_config(moving_home.sandbox_id)
            assert config["home_claim"] != moving_home.source
            assert config["node_name"] == "node-b"
            assert config["previous_home_claim"] == moving_home.source
            attempts.append(claim)
            if len(attempts) == 1:
                raise kube.KubeError(503, "source cleanup interrupted")
        delete_claim(namespace, claim, wait=wait)

    monkeypatch.setattr(kube, "delete_claim", fail_first_source_cleanup)
    with pytest.raises(kube.KubeError, match="source cleanup interrupted"):
        kube.move_volume(moving_home.sandbox_id, "node-b")

    config = kube.home_config(moving_home.sandbox_id)
    target = config["home_claim"]
    assert config["previous_home_claim"] == moving_home.source
    assert moving_home.claims == {moving_home.source: "node-a", target: "node-b"}
    assert moving_home.created == moving_home.copied == [target]
    assert len(moving_home.snapshots) == 1

    def unexpected_copy(*args, **kwargs):
        raise AssertionError("a committed move retry must only finish source cleanup")

    for name in ("snapshot", "ensure_claim", "restore_claim"):
        monkeypatch.setattr(kube, name, unexpected_copy)

    assert kube.move_volume(moving_home.sandbox_id, "node-b") == target
    config = kube.home_config(moving_home.sandbox_id)
    assert config["home_claim"] == target and config["node_name"] == "node-b"
    assert not config.get("previous_home_claim")
    assert moving_home.claims == {target: "node-b"}
    assert attempts == [moving_home.source, moving_home.source]
    assert moving_home.deleted == [moving_home.source]
    assert moving_home.created == moving_home.copied == [target]
    assert len(moving_home.snapshots) == 1


@pytest.mark.parametrize("parts", [["home.0001"], ["home.0000", "home.0002"]])
def test_restore_rejects_missing_archive_parts_before_starting_a_helper(monkeypatch, parts):
    prefix = kube.snapshot_prefix("incomplete")
    monkeypatch.setattr(objects, "keys", lambda requested: [prefix + part for part in parts])
    helper = Mock(side_effect=AssertionError("an incomplete snapshot must not touch the home claim"))
    monkeypatch.setattr(kube, "HelperPod", helper)

    with pytest.raises(ZooError, match="missing home archive parts") as error:
        kube.restore_claim("sandbox", "home", "incomplete", node="node-b")

    assert error.value.code == "upstream_error"
    helper.assert_not_called()


def test_move_rejects_a_changed_source_pointer_without_deleting_it(moving_home, monkeypatch):
    restore_claim = kube.restore_claim

    def change_pointer_after_copy(sandbox_id, claim, snapshot_id, node=""):
        restore_claim(sandbox_id, claim, snapshot_id, node=node)
        with db_manager.session() as db:
            stored = present(db.get_sandbox_for_update(id=sandbox_id))
            config = {**json.loads(stored.config), "home_claim": "another-home", "node_name": "node-c"}
            db.update_sandbox(name=stored.name, resources=stored.resources, config=json.dumps(config), id=sandbox_id)
        moving_home.claims["another-home"] = "node-c"

    monkeypatch.setattr(kube, "restore_claim", change_pointer_after_copy)
    with pytest.raises(ZooError) as error:
        kube.move_volume(moving_home.sandbox_id, "node-b")
    assert error.value.code == "conflict"
    assert kube.home_config(moving_home.sandbox_id)["home_claim"] == "another-home"
    assert moving_home.claims == {moving_home.source: "node-a", "another-home": "node-c"}
    assert moving_home.source not in moving_home.deleted
    assert moving_home.deleted == moving_home.created


def test_move_retry_clears_persisted_cleanup_state_after_the_source_is_already_gone(moving_home, monkeypatch):
    clear_previous = Querier.clear_previous_home_claim
    attempts = []

    def fail_first_clear(self, *, sandbox_id, home_claim, previous_home_claim):
        attempts.append(previous_home_claim)
        if len(attempts) == 1:
            return None
        return clear_previous(
            self, sandbox_id=sandbox_id, home_claim=home_claim, previous_home_claim=previous_home_claim
        )

    monkeypatch.setattr(Querier, "clear_previous_home_claim", fail_first_clear)
    with pytest.raises(ZooError) as error:
        kube.move_volume(moving_home.sandbox_id, "node-b")
    assert error.value.code == "conflict"
    config = kube.home_config(moving_home.sandbox_id)
    target = config["home_claim"]
    assert config["previous_home_claim"] == moving_home.source
    assert moving_home.claims == {target: "node-b"}
    assert kube.move_volume(moving_home.sandbox_id, "node-b") == target
    assert "previous_home_claim" not in kube.home_config(moving_home.sandbox_id)
    assert moving_home.created == moving_home.copied == [target]
    assert len(moving_home.snapshots) == 1
    assert attempts == [moving_home.source, moving_home.source]
