"""Tests for NotificationRuntime — generation, acknowledgement, and filtering."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from hermes.kernel.acknowledgement_store import AcknowledgementStore
from hermes.kernel.heartbeat_runtime import append_heartbeat
from hermes.kernel.heartbeat_store import HeartbeatStore
from hermes.kernel.notification_runtime import (
    apply_acknowledgements,
    filter_unread,
    generate_notifications,
)
from hermes.models.heartbeat import Heartbeat
from hermes.models.notification import (
    NOTIFICATION_CATEGORIES,
    NOTIFICATION_SEVERITIES,
    Notification,
)
from hermes.models.operation import Operation

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
EXAMPLES = ROOT / "examples"


def _make_operation(
    op_id: str = "OP-20260803-001",
    status: str = "executing",
    request: str = "Test request",
    created_at: datetime | None = None,
) -> Operation:
    now = created_at or datetime(2026, 8, 3, tzinfo=timezone.utc)
    return Operation(
        id=op_id,
        workspace_id="ws1",
        request=request,
        status=status,
        created_at=now,
        updated_at=now,
    )


# -- Mock CEO review data for notification generation --

@dataclass
class MockKPI:
    kpi_id: str
    name: str
    status: str
    current_value: str = "0"
    target_value: str = "100"


@dataclass
class MockBottleneck:
    bottleneck_id: str
    title: str
    status: str
    impact: str = "High"


@dataclass
class MockRecommendation:
    recommendation_id: str
    title: str
    context: str = "Review context"


@dataclass
class MockData:
    kpis: list
    bottlenecks: list


@dataclass
class MockEngineResult:
    recommendations: list


@dataclass
class MockReview:
    data: MockData
    engine_result: MockEngineResult


def _make_review(
    kpis: list | None = None,
    bottlenecks: list | None = None,
    recommendations: list | None = None,
) -> MockReview:
    return MockReview(
        data=MockData(
            kpis=kpis or [],
            bottlenecks=bottlenecks or [],
        ),
        engine_result=MockEngineResult(
            recommendations=recommendations or [],
        ),
    )


# -- Generation: Operation-sourced -------------------------------------------


class TestGenerateFromOperations:
    def test_escalated_operation_critical(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation(status="awaiting_escalation")
        notifs = generate_notifications([op], store, None, "ws1")
        escalated = [n for n in notifs if n.category == "operation_escalated"]
        assert len(escalated) == 1
        assert escalated[0].severity == "critical"
        assert "OP-20260803-001" in escalated[0].id

    def test_blocked_operation_critical(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation()
        append_heartbeat(store, op.id, "ws1", "blocked", "Stuck", blocker="Vendor delay")
        notifs = generate_notifications([op], store, None, "ws1")
        blocked = [n for n in notifs if n.category == "operation_blocked"]
        assert len(blocked) == 1
        assert blocked[0].severity == "critical"
        assert "Vendor delay" in blocked[0].summary

    def test_stale_operation_warning(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        old = datetime(2026, 8, 1, tzinfo=timezone.utc)
        op = _make_operation(created_at=old)
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        notifs = generate_notifications([op], store, None, "ws1", stale_threshold_hours=24, now=now)
        stale = [n for n in notifs if n.category == "operation_stale"]
        assert len(stale) == 1
        assert stale[0].severity == "warning"

    def test_completed_operation_no_notifications(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation(status="completed")
        notifs = generate_notifications([op], store, None, "ws1")
        assert len(notifs) == 0

    def test_failed_operation_no_notifications(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation(status="failed")
        notifs = generate_notifications([op], store, None, "ws1")
        assert len(notifs) == 0

    def test_rejected_operation_no_notifications(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation(status="rejected")
        notifs = generate_notifications([op], store, None, "ws1")
        assert len(notifs) == 0

    def test_recent_heartbeat_no_stale(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation()
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        recent = now - timedelta(hours=1)
        hb = Heartbeat(
            id="HB-001", operation_id=op.id, workspace_id="ws1",
            timestamp=recent, status="active", summary="Working",
        )
        store.save(hb)
        notifs = generate_notifications([op], store, None, "ws1", stale_threshold_hours=24, now=now)
        stale = [n for n in notifs if n.category == "operation_stale"]
        assert len(stale) == 0

    def test_empty_state_no_notifications(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        notifs = generate_notifications([], store, None, "ws1")
        assert notifs == []

    def test_no_heartbeat_store(self):
        op = _make_operation()
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        notifs = generate_notifications([op], None, None, "ws1", now=now)
        # No blocked or stale notifications without heartbeat store
        assert all(n.category == "operation_escalated" for n in notifs) or len(notifs) == 0


# -- Generation: Review-sourced -----------------------------------------------


class TestGenerateFromReview:
    def test_kpi_off_track_warning(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        review = _make_review(kpis=[MockKPI("kpi-1", "Revenue", "off_track")])
        notifs = generate_notifications([], store, review, "ws1")
        kpi_notifs = [n for n in notifs if n.category == "kpi_off_track"]
        assert len(kpi_notifs) == 1
        assert kpi_notifs[0].severity == "warning"
        assert "Revenue" in kpi_notifs[0].title

    def test_kpi_at_risk_info(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        review = _make_review(kpis=[MockKPI("kpi-2", "Users", "at_risk")])
        notifs = generate_notifications([], store, review, "ws1")
        kpi_notifs = [n for n in notifs if n.category == "kpi_at_risk"]
        assert len(kpi_notifs) == 1
        assert kpi_notifs[0].severity == "info"

    def test_kpi_on_track_no_notification(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        review = _make_review(kpis=[MockKPI("kpi-3", "NPS", "on_track")])
        notifs = generate_notifications([], store, review, "ws1")
        assert len(notifs) == 0

    def test_active_bottleneck_warning(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        review = _make_review(bottlenecks=[MockBottleneck("bot-1", "Hiring", "active")])
        notifs = generate_notifications([], store, review, "ws1")
        bot_notifs = [n for n in notifs if n.category == "bottleneck"]
        assert len(bot_notifs) == 1
        assert bot_notifs[0].severity == "warning"

    def test_resolved_bottleneck_no_notification(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        review = _make_review(bottlenecks=[MockBottleneck("bot-2", "Old", "resolved")])
        notifs = generate_notifications([], store, review, "ws1")
        assert len(notifs) == 0

    def test_recommendation_info(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        review = _make_review(recommendations=[MockRecommendation("rec-1", "Expand market")])
        notifs = generate_notifications([], store, review, "ws1")
        rec_notifs = [n for n in notifs if n.category == "recommendation"]
        assert len(rec_notifs) == 1
        assert rec_notifs[0].severity == "info"

    def test_no_review_still_works(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        notifs = generate_notifications([], store, None, "ws1")
        assert notifs == []


# -- Deterministic IDs ---------------------------------------------------------


class TestDeterministicIds:
    def test_same_condition_same_id(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation(status="awaiting_escalation")
        now = datetime(2026, 8, 3, tzinfo=timezone.utc)
        n1 = generate_notifications([op], store, None, "ws1", now=now)
        n2 = generate_notifications([op], store, None, "ws1", now=now)
        assert n1[0].id == n2[0].id

    def test_different_operations_different_ids(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op1 = _make_operation(op_id="OP-A", status="awaiting_escalation")
        op2 = _make_operation(op_id="OP-B", status="awaiting_escalation")
        notifs = generate_notifications([op1, op2], store, None, "ws1")
        ids = [n.id for n in notifs]
        assert len(set(ids)) == len(ids)  # All unique


# -- Sorting -------------------------------------------------------------------


class TestNotificationSorting:
    def test_critical_before_warning_before_info(self, tmp_path):
        store = HeartbeatStore(workspaces_root=tmp_path)
        op = _make_operation(status="awaiting_escalation")
        review = _make_review(
            kpis=[MockKPI("kpi-1", "Revenue", "off_track"), MockKPI("kpi-2", "Users", "at_risk")],
        )
        old = datetime(2026, 8, 1, tzinfo=timezone.utc)
        stale_op = _make_operation(op_id="OP-STALE", created_at=old)
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        notifs = generate_notifications([op, stale_op], store, review, "ws1", now=now)

        severities = [n.severity for n in notifs]
        # All criticals before all warnings before all infos
        assert severities == sorted(severities, key=lambda s: {"critical": 0, "warning": 1, "info": 2}[s])


# -- Acknowledgements ---------------------------------------------------------


class TestAcknowledgementStore:
    def test_save_and_load(self, tmp_path):
        store = AcknowledgementStore(workspaces_root=tmp_path)
        store.acknowledge("ws1", "notif-test-1")
        acked = store.load("ws1")
        assert "notif-test-1" in acked

    def test_load_empty(self, tmp_path):
        store = AcknowledgementStore(workspaces_root=tmp_path)
        assert store.load("ws1") == set()

    def test_idempotent_acknowledge(self, tmp_path):
        store = AcknowledgementStore(workspaces_root=tmp_path)
        store.acknowledge("ws1", "notif-test-1")
        store.acknowledge("ws1", "notif-test-1")
        acked = store.load("ws1")
        assert len(acked) == 1

    def test_multiple_acknowledgements(self, tmp_path):
        store = AcknowledgementStore(workspaces_root=tmp_path)
        store.acknowledge("ws1", "notif-a")
        store.acknowledge("ws1", "notif-b")
        acked = store.load("ws1")
        assert acked == {"notif-a", "notif-b"}


class TestApplyAcknowledgements:
    def test_sets_acknowledged(self):
        now = datetime(2026, 8, 3, tzinfo=timezone.utc)
        n1 = Notification(
            id="notif-1", timestamp=now, severity="info",
            category="recommendation", title="T1", summary="S1",
            related_object_type="recommendation", related_object_id="rec-1",
        )
        n2 = Notification(
            id="notif-2", timestamp=now, severity="warning",
            category="bottleneck", title="T2", summary="S2",
            related_object_type="bottleneck", related_object_id="bot-1",
        )
        apply_acknowledgements([n1, n2], {"notif-1"})
        assert n1.acknowledged is True
        assert n2.acknowledged is False

    def test_empty_acked_set(self):
        now = datetime(2026, 8, 3, tzinfo=timezone.utc)
        n1 = Notification(
            id="notif-1", timestamp=now, severity="info",
            category="recommendation", title="T", summary="S",
            related_object_type="recommendation", related_object_id="r",
        )
        apply_acknowledgements([n1], set())
        assert n1.acknowledged is False


class TestFilterUnread:
    def test_excludes_acknowledged(self):
        now = datetime(2026, 8, 3, tzinfo=timezone.utc)
        n1 = Notification(
            id="notif-1", timestamp=now, severity="info",
            category="recommendation", title="T1", summary="S1",
            related_object_type="recommendation", related_object_id="r1",
            acknowledged=True,
        )
        n2 = Notification(
            id="notif-2", timestamp=now, severity="warning",
            category="bottleneck", title="T2", summary="S2",
            related_object_type="bottleneck", related_object_id="b1",
        )
        unread = filter_unread([n1, n2])
        assert len(unread) == 1
        assert unread[0].id == "notif-2"


# -- Model constants ----------------------------------------------------------


class TestNotificationConstants:
    def test_severities(self):
        assert NOTIFICATION_SEVERITIES == {"info", "warning", "critical"}

    def test_categories(self):
        assert NOTIFICATION_CATEGORIES == {
            "operation_blocked", "operation_stale", "operation_escalated",
            "kpi_off_track", "kpi_at_risk", "recommendation", "bottleneck",
        }


# -- Schema & Example ---------------------------------------------------------


class TestNotificationSchema:
    def test_example_validates_against_schema(self):
        schema = json.loads((CONTRACTS / "notification.schema.json").read_text())
        example = json.loads((EXAMPLES / "notification.example.json").read_text())
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(example), key=lambda e: list(e.path))
        assert not errors, f"Schema validation errors: {[e.message for e in errors]}"

    def test_schema_required_fields(self):
        schema = json.loads((CONTRACTS / "notification.schema.json").read_text())
        assert set(schema["required"]) == {
            "id", "timestamp", "severity", "category",
            "title", "summary", "related_object_type", "related_object_id",
        }

    def test_schema_severity_enum(self):
        schema = json.loads((CONTRACTS / "notification.schema.json").read_text())
        assert set(schema["properties"]["severity"]["enum"]) == {"info", "warning", "critical"}

    def test_schema_category_enum(self):
        schema = json.loads((CONTRACTS / "notification.schema.json").read_text())
        assert set(schema["properties"]["category"]["enum"]) == NOTIFICATION_CATEGORIES
