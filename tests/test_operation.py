from datetime import datetime, timezone

import pytest
import yaml

from hermes.kernel.operation_id import generate_operation_id
from hermes.kernel.operation_store import OperationNotFoundError, OperationStore
from hermes.models import InvalidTransitionError, Operation, transition_operation


def _make_operation(workspace_id="TEST", **kwargs):
    now = datetime.now(timezone.utc)
    defaults = {
        "id": "OP-20260801-001",
        "workspace_id": workspace_id,
        "request": "Generate homepage copy",
        "status": "created",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(kwargs)
    return Operation(**defaults)


# -- Model tests -----------------------------------------------------------


def test_operation_has_required_fields():
    op = _make_operation()
    assert op.id == "OP-20260801-001"
    assert op.workspace_id == "TEST"
    assert op.request == "Generate homepage copy"
    assert op.status == "created"
    assert isinstance(op.created_at, datetime)
    assert isinstance(op.updated_at, datetime)


def test_operation_default_extra_fields_is_empty():
    op = _make_operation()
    assert op.extra_fields == {}


def test_operation_timestamps_are_datetime():
    op = _make_operation()
    assert isinstance(op.created_at, datetime)
    assert isinstance(op.updated_at, datetime)


# -- ID generator tests ----------------------------------------------------


def test_generate_id_first_of_day(tmp_path):
    ops_dir = tmp_path / "operations"
    ops_dir.mkdir()
    op_id = generate_operation_id(ops_dir)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert op_id == f"OP-{today}-001"


def test_generate_id_increments_sequence(tmp_path):
    ops_dir = tmp_path / "operations"
    ops_dir.mkdir()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    (ops_dir / f"OP-{today}-001.yaml").touch()
    (ops_dir / f"OP-{today}-002.yaml").touch()
    op_id = generate_operation_id(ops_dir)
    assert op_id == f"OP-{today}-003"


def test_generate_id_nonexistent_directory(tmp_path):
    ops_dir = tmp_path / "does-not-exist"
    op_id = generate_operation_id(ops_dir)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert op_id == f"OP-{today}-001"


def test_generate_id_ignores_non_yaml(tmp_path):
    ops_dir = tmp_path / "operations"
    ops_dir.mkdir()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    (ops_dir / f"OP-{today}-001.txt").touch()
    op_id = generate_operation_id(ops_dir)
    assert op_id == f"OP-{today}-001"


# -- Store tests -----------------------------------------------------------


def test_save_creates_yaml_file(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    op = _make_operation()
    store.save(op)
    path = tmp_path / "TEST" / "operations" / "OP-20260801-001.yaml"
    assert path.is_file()


def test_save_creates_operations_directory(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    op = _make_operation()
    store.save(op)
    assert (tmp_path / "TEST" / "operations").is_dir()


def test_load_returns_saved_operation(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    op = _make_operation()
    store.save(op)
    loaded = store.load("TEST", "OP-20260801-001")
    assert loaded.id == op.id
    assert loaded.workspace_id == op.workspace_id
    assert loaded.request == op.request
    assert loaded.status == op.status


def test_load_nonexistent_raises(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    with pytest.raises(OperationNotFoundError):
        store.load("TEST", "OP-99999999-999")


def test_list_returns_all_operations(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    op1 = _make_operation(id="OP-20260801-001")
    op2 = _make_operation(id="OP-20260801-002")
    store.save(op1)
    store.save(op2)
    ops = store.list("TEST")
    assert len(ops) == 2
    ids = [o.id for o in ops]
    assert "OP-20260801-001" in ids
    assert "OP-20260801-002" in ids


def test_list_empty_workspace_returns_empty(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    assert store.list("NONEXISTENT") == []


def test_save_includes_version(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    op = _make_operation()
    store.save(op)
    path = tmp_path / "TEST" / "operations" / "OP-20260801-001.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    assert data["version"] == 1


def test_save_preserves_unknown_fields(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    op = _make_operation(extra_fields={"future_field": "future_value", "tags": ["alpha"]})
    store.save(op)
    loaded = store.load("TEST", "OP-20260801-001")
    assert loaded.extra_fields["future_field"] == "future_value"
    assert loaded.extra_fields["tags"] == ["alpha"]


def test_round_trip_preserves_unknown_fields_after_modification(tmp_path):
    """Load, modify a known field, save — unknown fields must survive."""
    store = OperationStore(workspaces_root=tmp_path)

    # Write YAML with an unknown field directly
    ops_dir = tmp_path / "TEST" / "operations"
    ops_dir.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    data = {
        "version": 1,
        "id": "OP-20260801-001",
        "workspace_id": "TEST",
        "request": "Original request",
        "status": "created",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "newer_hermes_field": {"nested": True},
    }
    with open(ops_dir / "OP-20260801-001.yaml", "w") as f:
        yaml.safe_dump(data, f)

    # Load, modify, save
    op = store.load("TEST", "OP-20260801-001")
    op.request = "Modified request"
    store.save(op)

    # Reload and verify
    reloaded = store.load("TEST", "OP-20260801-001")
    assert reloaded.request == "Modified request"
    assert reloaded.extra_fields["newer_hermes_field"] == {"nested": True}


def test_list_sorted_by_filename(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    op2 = _make_operation(id="OP-20260801-002")
    op1 = _make_operation(id="OP-20260801-001")
    store.save(op2)
    store.save(op1)
    ops = store.list("TEST")
    assert ops[0].id == "OP-20260801-001"
    assert ops[1].id == "OP-20260801-002"


# -- Lifecycle tests -------------------------------------------------------


def test_transition_created_to_executing():
    op = _make_operation(status="created")
    transition_operation(op, "executing")
    assert op.status == "executing"


def test_transition_executing_to_completed():
    op = _make_operation(status="executing")
    transition_operation(op, "completed")
    assert op.status == "completed"


def test_transition_executing_to_awaiting_escalation():
    op = _make_operation(status="executing")
    transition_operation(op, "awaiting_escalation")
    assert op.status == "awaiting_escalation"


def test_transition_executing_to_failed():
    op = _make_operation(status="executing")
    transition_operation(op, "failed")
    assert op.status == "failed"


def test_transition_awaiting_escalation_to_executing():
    op = _make_operation(status="awaiting_escalation")
    transition_operation(op, "executing")
    assert op.status == "executing"


def test_transition_awaiting_escalation_to_rejected():
    op = _make_operation(status="awaiting_escalation")
    transition_operation(op, "rejected")
    assert op.status == "rejected"


def test_transition_invalid_raises():
    op = _make_operation(status="created")
    with pytest.raises(InvalidTransitionError):
        transition_operation(op, "completed")


def test_transition_from_terminal_raises():
    for terminal in ["completed", "rejected", "failed"]:
        op = _make_operation(status=terminal)
        with pytest.raises(InvalidTransitionError):
            transition_operation(op, "executing")


def test_transition_updates_timestamp():
    op = _make_operation(status="created")
    original = op.updated_at
    transition_operation(op, "executing")
    assert op.updated_at >= original


def test_transition_persists_new_status(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    op = _make_operation(status="created")
    store.save(op)
    transition_operation(op, "executing")
    store.save(op)
    loaded = store.load("TEST", op.id)
    assert loaded.status == "executing"


# -- Business acceptance tests ---------------------------------------------


def test_operation_round_trip_in_workspace(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    op_id = generate_operation_id(store.operations_dir("ACME"))
    now = datetime.now(timezone.utc)
    op = Operation(
        id=op_id,
        workspace_id="ACME",
        request="Analyze market positioning for Q3",
        status="created",
        created_at=now,
        updated_at=now,
    )
    store.save(op)

    ops = store.list("ACME")
    assert len(ops) == 1
    assert ops[0].id == op_id

    loaded = store.load("ACME", op_id)
    assert loaded.workspace_id == "ACME"
    assert loaded.request == "Analyze market positioning for Q3"
    assert loaded.status == "created"


def test_operation_lifecycle_full_path(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    op = _make_operation()
    store.save(op)

    transition_operation(op, "executing")
    store.save(op)
    assert store.load("TEST", op.id).status == "executing"

    transition_operation(op, "completed")
    store.save(op)
    assert store.load("TEST", op.id).status == "completed"


def test_operation_escalation_path(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    op = _make_operation()

    transition_operation(op, "executing")
    transition_operation(op, "awaiting_escalation")
    transition_operation(op, "executing")
    transition_operation(op, "completed")
    store.save(op)
    assert store.load("TEST", op.id).status == "completed"


def test_operation_rejection_path(tmp_path):
    store = OperationStore(workspaces_root=tmp_path)
    op = _make_operation()

    transition_operation(op, "executing")
    transition_operation(op, "awaiting_escalation")
    transition_operation(op, "rejected")
    store.save(op)
    assert store.load("TEST", op.id).status == "rejected"
