"""Tests for HeartbeatRuntime — append, retrieve, freshness, and stale detection."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from hermes.kernel.heartbeat_id import generate_heartbeat_id
from hermes.kernel.heartbeat_runtime import (
    InvalidHeartbeatStatusError,
    append_heartbeat,
    compute_freshness,
    find_stale_operations,
    get_latest,
    list_heartbeats,
)
from hermes.kernel.heartbeat_store import HeartbeatNotFoundError, HeartbeatStore
from hermes.models.heartbeat import HEARTBEAT_STATUSES, Heartbeat
from hermes.models.operation import Operation

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
EXAMPLES = ROOT / "examples"


def _make_operation(
    op_id: str = "OP-20260803-001",
    workspace_id: str = "ws1",
    status: str = "executing",
    created_at: datetime | None = None,
) -> Operation:
    now = created_at or datetime(2026, 8, 3, tzinfo=timezone.utc)
    return Operation(
        id=op_id,
        workspace_id=workspace_id,
        request="Test request",
        status=status,
        created_at=now,
        updated_at=now,
    )


def _make_heartbeat(
    hb_id: str = "HB-001",
    operation_id: str = "OP-20260803-001",
    workspace_id: str = "ws1",
    timestamp: datetime | None = None,
    status: str = "active",
    summary: str = "Test heartbeat",
    author: str = "Founder",
) -> Heartbeat:
    ts = timestamp or datetime(2026, 8, 3, 14, 30, tzinfo=timezone.utc)
    return Heartbeat(
        id=hb_id,
        operation_id=operation_id,
        workspace_id=workspace_id,
        timestamp=ts,
        status=status,
        summary=summary,
        author=author,
    )


# -- Heartbeat ID Generation --------------------------------------------------


class TestHeartbeatIdGeneration:
    def test_first_id_in_empty_dir(self, tmp_path):
        hb_dir = tmp_path / "heartbeats"
        hb_dir.mkdir()
        assert generate_heartbeat_id(hb_dir) == "HB-001"

    def test_sequential_ids(self, tmp_path):
        hb_dir = tmp_path / "heartbeats"
        hb_dir.mkdir()
        (hb_dir / "HB-001.yaml").touch()
        assert generate_heartbeat_id(hb_dir) == "HB-002"

    def test_finds_max_across_gaps(self, tmp_path):
        hb_dir = tmp_path / "heartbeats"
        hb_dir.mkdir()
        (hb_dir / "HB-001.yaml").touch()
        (hb_dir / "HB-005.yaml").touch()
        assert generate_heartbeat_id(hb_dir) == "HB-006"

    def test_nonexistent_dir(self, tmp_path):
        hb_dir = tmp_path / "nonexistent"
        assert generate_heartbeat_id(hb_dir) == "HB-001"


# -- HeartbeatStore -----------------------------------------------------------


class TestHeartbeatStore:
    def test_save_and_load(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        hb = _make_heartbeat()
        store.save(hb)
        loaded = store.load("ws1", "HB-001")
        assert loaded.id == "HB-001"
        assert loaded.operation_id == "OP-20260803-001"
        assert loaded.status == "active"
        assert loaded.summary == "Test heartbeat"
        assert loaded.author == "Founder"

    def test_load_nonexistent_raises(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        with pytest.raises(HeartbeatNotFoundError):
            store.load("ws1", "HB-999")

    def test_list_empty(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        assert store.list("ws1") == []

    def test_list_returns_all(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        hb1 = _make_heartbeat(hb_id="HB-001")
        hb2 = _make_heartbeat(hb_id="HB-002", summary="Second")
        store.save(hb1)
        store.save(hb2)
        result = store.list("ws1")
        assert len(result) == 2

    def test_list_by_operation_filters(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        hb1 = _make_heartbeat(hb_id="HB-001", operation_id="OP-A")
        hb2 = _make_heartbeat(hb_id="HB-002", operation_id="OP-B")
        store.save(hb1)
        store.save(hb2)
        result = store.list_by_operation("ws1", "OP-A")
        assert len(result) == 1
        assert result[0].operation_id == "OP-A"

    def test_list_by_operation_newest_first(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        t1 = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
        hb1 = _make_heartbeat(hb_id="HB-001", timestamp=t1)
        hb2 = _make_heartbeat(hb_id="HB-002", timestamp=t2)
        store.save(hb1)
        store.save(hb2)
        result = store.list_by_operation("ws1", "OP-20260803-001")
        assert result[0].id == "HB-002"
        assert result[1].id == "HB-001"

    def test_round_trip_preserves_optional_fields(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        hb = _make_heartbeat()
        hb.details = "Some details"
        hb.blocker = "Waiting for approval"
        hb.next_action = "Follow up Monday"
        store.save(hb)
        loaded = store.load("ws1", "HB-001")
        assert loaded.details == "Some details"
        assert loaded.blocker == "Waiting for approval"
        assert loaded.next_action == "Follow up Monday"


# -- HeartbeatRuntime: append --------------------------------------------------


class TestAppendHeartbeat:
    def test_creates_heartbeat(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        hb = append_heartbeat(
            store, "OP-001", "ws1",
            status="active",
            summary="Started work",
            author="Founder",
        )
        assert hb.id == "HB-001"
        assert hb.operation_id == "OP-001"
        assert hb.status == "active"
        assert hb.author == "Founder"

    def test_persists_to_disk(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        hb = append_heartbeat(store, "OP-001", "ws1", "active", "Test")
        loaded = store.load("ws1", hb.id)
        assert loaded.summary == "Test"

    def test_increments_id(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        hb1 = append_heartbeat(store, "OP-001", "ws1", "active", "First")
        hb2 = append_heartbeat(store, "OP-001", "ws1", "active", "Second")
        assert hb1.id == "HB-001"
        assert hb2.id == "HB-002"

    def test_invalid_status_raises(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        with pytest.raises(InvalidHeartbeatStatusError, match="invalid_status"):
            append_heartbeat(store, "OP-001", "ws1", "invalid_status", "Test")

    def test_completed_status_rejected(self, tmp_path):
        """Amendment: 'completed' is not a valid heartbeat status."""
        store = HeartbeatStore(workspaces_root=tmp_path)
        with pytest.raises(InvalidHeartbeatStatusError):
            append_heartbeat(store, "OP-001", "ws1", "completed", "Done")

    def test_all_valid_statuses_accepted(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        for status in sorted(HEARTBEAT_STATUSES):
            hb = append_heartbeat(store, "OP-001", "ws1", status, f"Status: {status}")
            assert hb.status == status

    def test_optional_fields(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        hb = append_heartbeat(
            store, "OP-001", "ws1", "blocked", "Blocked",
            blocker="Waiting on vendor",
            next_action="Follow up Thursday",
            details="Vendor hasn't responded to email",
        )
        assert hb.blocker == "Waiting on vendor"
        assert hb.next_action == "Follow up Thursday"
        assert hb.details == "Vendor hasn't responded to email"


# -- HeartbeatRuntime: get_latest / list_heartbeats ----------------------------


class TestGetLatest:
    def test_returns_none_when_empty(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        assert get_latest(store, "ws1", "OP-001") is None

    def test_returns_most_recent(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        append_heartbeat(store, "OP-001", "ws1", "active", "First")
        append_heartbeat(store, "OP-001", "ws1", "blocked", "Second")
        latest = get_latest(store, "ws1", "OP-001")
        assert latest.summary == "Second"
        assert latest.status == "blocked"


class TestListHeartbeats:
    def test_newest_first(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        append_heartbeat(store, "OP-001", "ws1", "active", "First")
        append_heartbeat(store, "OP-001", "ws1", "active", "Second")
        hbs = list_heartbeats(store, "ws1", "OP-001")
        assert len(hbs) == 2
        assert hbs[0].summary == "Second"

    def test_filters_by_operation(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        append_heartbeat(store, "OP-A", "ws1", "active", "Op A")
        append_heartbeat(store, "OP-B", "ws1", "active", "Op B")
        hbs = list_heartbeats(store, "ws1", "OP-A")
        assert len(hbs) == 1
        assert hbs[0].operation_id == "OP-A"


# -- HeartbeatRuntime: compute_freshness ---------------------------------------


class TestComputeFreshness:
    def test_returns_correct_timedelta(self):
        ts = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 8, 3, 14, 30, tzinfo=timezone.utc)
        hb = _make_heartbeat(timestamp=ts)
        delta = compute_freshness(hb, now=now)
        assert delta == timedelta(hours=4, minutes=30)

    def test_uses_utc_now_as_default(self):
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        hb = _make_heartbeat(timestamp=recent)
        delta = compute_freshness(hb)
        assert delta.total_seconds() < 600  # Less than 10 minutes


# -- HeartbeatRuntime: find_stale_operations -----------------------------------


class TestFindStaleOperations:
    def test_identifies_stale_with_old_heartbeat(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation()
        old_ts = datetime(2026, 8, 1, tzinfo=timezone.utc)
        hb = _make_heartbeat(timestamp=old_ts)
        store.save(hb)

        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        stale = find_stale_operations([op], store, "ws1", threshold_hours=24, now=now)
        assert len(stale) == 1
        assert stale[0].operation_id == "OP-20260803-001"
        assert stale[0].elapsed_hours > 48

    def test_identifies_stale_with_no_heartbeats(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        old_created = datetime(2026, 8, 1, tzinfo=timezone.utc)
        op = _make_operation(created_at=old_created)

        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        stale = find_stale_operations([op], store, "ws1", threshold_hours=24, now=now)
        assert len(stale) == 1
        assert stale[0].latest_heartbeat is None

    def test_excludes_recent_heartbeat(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation()
        recent = datetime(2026, 8, 3, 11, 0, tzinfo=timezone.utc)
        hb = _make_heartbeat(timestamp=recent)
        store.save(hb)

        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        stale = find_stale_operations([op], store, "ws1", threshold_hours=24, now=now)
        assert len(stale) == 0

    def test_excludes_completed_operations(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation(status="completed")

        now = datetime(2026, 8, 5, tzinfo=timezone.utc)
        stale = find_stale_operations([op], store, "ws1", threshold_hours=24, now=now)
        assert len(stale) == 0

    def test_excludes_failed_operations(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation(status="failed")

        now = datetime(2026, 8, 5, tzinfo=timezone.utc)
        stale = find_stale_operations([op], store, "ws1", threshold_hours=24, now=now)
        assert len(stale) == 0

    def test_excludes_rejected_operations(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation(status="rejected")

        now = datetime(2026, 8, 5, tzinfo=timezone.utc)
        stale = find_stale_operations([op], store, "ws1", threshold_hours=24, now=now)
        assert len(stale) == 0

    def test_includes_awaiting_escalation(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        old_created = datetime(2026, 8, 1, tzinfo=timezone.utc)
        op = _make_operation(status="awaiting_escalation", created_at=old_created)

        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        stale = find_stale_operations([op], store, "ws1", threshold_hours=24, now=now)
        assert len(stale) == 1

    def test_configurable_threshold(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation()
        ts = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
        hb = _make_heartbeat(timestamp=ts)
        store.save(hb)

        now = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
        # 4 hours elapsed — stale at 2h threshold, not stale at 8h
        stale_2h = find_stale_operations([op], store, "ws1", threshold_hours=2, now=now)
        stale_8h = find_stale_operations([op], store, "ws1", threshold_hours=8, now=now)
        assert len(stale_2h) == 1
        assert len(stale_8h) == 0

    def test_elapsed_hours_correct(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        old_created = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        op = _make_operation(created_at=old_created)

        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        stale = find_stale_operations([op], store, "ws1", threshold_hours=24, now=now)
        assert stale[0].elapsed_hours == 48.0


# -- Heartbeat Statuses -------------------------------------------------------


class TestHeartbeatStatuses:
    def test_active_blocked_waiting_only(self):
        assert HEARTBEAT_STATUSES == {"active", "blocked", "waiting"}

    def test_completed_not_in_statuses(self):
        """Amendment: completed is NOT a valid heartbeat status."""
        assert "completed" not in HEARTBEAT_STATUSES


# -- Schema & Example Validation -----------------------------------------------


class TestHeartbeatSchema:
    def test_example_validates_against_schema(self):
        schema = json.loads((CONTRACTS / "heartbeat.schema.json").read_text())
        example = json.loads((EXAMPLES / "heartbeat.example.json").read_text())
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(example), key=lambda e: list(e.path))
        assert not errors, f"Schema validation errors: {[e.message for e in errors]}"

    def test_schema_required_fields(self):
        schema = json.loads((CONTRACTS / "heartbeat.schema.json").read_text())
        assert set(schema["required"]) == {
            "id", "operation_id", "workspace_id", "timestamp", "status", "summary",
        }

    def test_schema_status_enum(self):
        schema = json.loads((CONTRACTS / "heartbeat.schema.json").read_text())
        assert set(schema["properties"]["status"]["enum"]) == {"active", "blocked", "waiting"}
