"""Labels on sandboxes: short key=value pairs (end_user=alice, team=support) that a builder puts on the sandboxes it
makes for its own customers. They filter lists (GET /sandboxes?label=end_user=alice), scope API keys (a key with the
selector {"end_user": "alice"} only sees and makes Alice's sandboxes), carry quotas per value (server/quotas.py) and
group usage (server/metering.py), which keeps a copy of each sandbox's labels after it is deleted."""

import json
import re

from fastapi import HTTPException

from db.generated.query import Querier

MAX_LABELS = 20
KEY = re.compile(r"^[a-z0-9][a-z0-9_./-]{0,62}$")
MAX_VALUE = 255
# a label quota's value for every value of its key (server/quotas.py), so no label may have it
ANY = "*"


def validate(labels: dict[str, str]) -> dict[str, str]:
    """The labels, or ValueError saying what is wrong with them (for pydantic validators)."""
    if len(labels) > MAX_LABELS:
        raise ValueError(f"at most {MAX_LABELS} labels")
    for key, value in labels.items():
        if not KEY.match(key):
            raise ValueError(f"label key {key!r}: 1-63 of a-z, 0-9, _ . / -, starting with a letter or digit")
        if not value or len(value) > MAX_VALUE or value == ANY:
            raise ValueError(f"label {key}: the value must be 1-{MAX_VALUE} characters and not {ANY!r}")
    return labels


def parse_filters(filters: list[str]) -> dict[str, str]:
    """`key=value` query parameters as a selector; 422 for one without `=`."""
    selector = {}
    for item in filters:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            raise HTTPException(status_code=422, detail=f"label filter {item!r} is not key=value")
        selector[key] = value
    return selector


def parse_selector(text: str | None) -> dict[str, str] | None:
    return json.loads(text) if text else None


def matches(labels: dict[str, str], selector: dict[str, str] | None) -> bool:
    return not selector or all(labels.get(key) == value for key, value in selector.items())


def of(db: Querier, sandbox_id: str) -> dict[str, str]:
    return {row.key: row.value for row in db.list_sandbox_labels(sandbox_id=sandbox_id)}


def of_workspace(db: Querier, workspace_id: str) -> dict[str, dict[str, str]]:
    """Every live sandbox's labels in the workspace, by sandbox id (a sandbox without labels is left out)."""
    found: dict[str, dict[str, str]] = {}
    for row in db.list_workspace_sandbox_labels(workspace_id=workspace_id):
        found.setdefault(row.sandbox_id, {})[row.key] = row.value
    return found


def put(db: Querier, sandbox_id: str, labels: dict[str, str]):
    """Replaces the sandbox's labels, and the copy that usage keeps."""
    db.delete_sandbox_labels(sandbox_id=sandbox_id)
    db.delete_usage_labels(sandbox_id=sandbox_id)
    for key, value in labels.items():
        db.set_sandbox_label(sandbox_id=sandbox_id, key=key, value=value)
        db.set_usage_label(sandbox_id=sandbox_id, key=key, value=value)


def scoped(labels: dict[str, str], selector: dict[str, str] | None) -> dict[str, str]:
    """The labels a key with `selector` puts on a sandbox: the selector's, which the request may repeat but not
    change. 403 when it tries to."""
    if not selector:
        return labels
    for key, value in selector.items():
        if labels.get(key, value) != value:
            raise HTTPException(status_code=403, detail=f"this API key only reaches sandboxes labelled {key}={value}")
    return {**labels, **selector}
