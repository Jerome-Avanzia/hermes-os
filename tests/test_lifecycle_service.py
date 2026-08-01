"""Tests for the generic Entity Lifecycle Service (Sprint 21).

All tests use test-local transition configurations.
No production entity types are registered.
"""

from dataclasses import dataclass
from datetime import datetime

import pytest

from hermes.kernel.lifecycle_service import (
    EntityLifecycleConfig,
    InvalidLifecycleTransition,
    LifecycleService,
)
from hermes.models import TransitionRecord


# -- Test-local fixtures -----------------------------------------------------


TEST_TRANSITIONS = {
    "alpha": ["beta"],
    "beta": ["gamma", "rejected"],
    # gamma and rejected are terminal
}

TEST_CONFIG = EntityLifecycleConfig(
    entity_type="widget",
    valid_transitions=TEST_TRANSITIONS,
    id_field="widget_id",
)


@dataclass
class Widget:
    widget_id: str
    status: str


def _service() -> LifecycleService:
    return LifecycleService(configs={"widget": TEST_CONFIG})


# -- Engine validation -------------------------------------------------------


def test_valid_transition_succeeds():
    svc = _service()
    w = Widget(widget_id="W-001", status="alpha")
    svc.transition("widget", w, "beta")
    assert w.status == "beta"


def test_invalid_transition_raises():
    svc = _service()
    w = Widget(widget_id="W-001", status="alpha")
    with pytest.raises(InvalidLifecycleTransition, match="alpha.*gamma"):
        svc.transition("widget", w, "gamma")


def test_terminal_state_raises():
    svc = _service()
    w = Widget(widget_id="W-001", status="gamma")
    with pytest.raises(InvalidLifecycleTransition, match="gamma.*alpha"):
        svc.transition("widget", w, "alpha")


def test_unknown_entity_type_raises():
    svc = _service()
    w = Widget(widget_id="W-001", status="alpha")
    with pytest.raises(KeyError):
        svc.transition("nonexistent", w, "beta")


def test_get_allowed_transitions_returns_list():
    svc = _service()
    allowed = svc.get_allowed_transitions("widget", "beta")
    assert allowed == ["gamma", "rejected"]


def test_get_allowed_transitions_terminal_returns_empty():
    svc = _service()
    assert svc.get_allowed_transitions("widget", "gamma") == []


# -- TransitionRecord --------------------------------------------------------


def test_transition_returns_record():
    svc = _service()
    w = Widget(widget_id="W-001", status="alpha")
    record = svc.transition("widget", w, "beta")
    assert isinstance(record, TransitionRecord)


def test_record_from_status():
    svc = _service()
    w = Widget(widget_id="W-001", status="alpha")
    record = svc.transition("widget", w, "beta")
    assert record.from_status == "alpha"


def test_record_to_status():
    svc = _service()
    w = Widget(widget_id="W-001", status="alpha")
    record = svc.transition("widget", w, "beta")
    assert record.to_status == "beta"


def test_record_triggered_by_and_reason():
    svc = _service()
    w = Widget(widget_id="W-001", status="alpha")
    record = svc.transition(
        "widget", w, "beta", triggered_by="test_user", reason="testing"
    )
    assert record.triggered_by == "test_user"
    assert record.reason == "testing"


def test_record_timestamp_is_iso8601():
    svc = _service()
    w = Widget(widget_id="W-001", status="alpha")
    record = svc.transition("widget", w, "beta")
    parsed = datetime.fromisoformat(record.timestamp)
    assert isinstance(parsed, datetime)


def test_record_entity_type_and_id():
    svc = _service()
    w = Widget(widget_id="W-001", status="alpha")
    record = svc.transition("widget", w, "beta")
    assert record.entity_type == "widget"
    assert record.entity_id == "W-001"


# -- Statelessness -----------------------------------------------------------


def test_consecutive_transitions_return_independent_records():
    svc = _service()
    w = Widget(widget_id="W-001", status="alpha")
    r1 = svc.transition("widget", w, "beta")
    r2 = svc.transition("widget", w, "gamma")
    assert r1.from_status == "alpha"
    assert r1.to_status == "beta"
    assert r2.from_status == "beta"
    assert r2.to_status == "gamma"
    assert r1 is not r2


def test_service_holds_no_state_between_calls():
    svc = _service()
    w1 = Widget(widget_id="W-001", status="alpha")
    w2 = Widget(widget_id="W-002", status="alpha")
    svc.transition("widget", w1, "beta")
    r2 = svc.transition("widget", w2, "beta")
    assert r2.entity_id == "W-002"
    assert r2.from_status == "alpha"


# -- Generic entity support --------------------------------------------------


def test_works_with_custom_status_field():
    @dataclass
    class Gadget:
        gadget_id: str
        phase: str

    config = EntityLifecycleConfig(
        entity_type="gadget",
        valid_transitions={"new": ["ready"], "ready": ["done"]},
        status_field="phase",
        id_field="gadget_id",
    )
    svc = LifecycleService(configs={"gadget": config})
    g = Gadget(gadget_id="G-001", phase="new")
    record = svc.transition("gadget", g, "ready")
    assert g.phase == "ready"
    assert record.from_status == "new"
    assert record.to_status == "ready"


def test_works_with_plain_object():
    class Plain:
        def __init__(self):
            self.id = "P-001"
            self.status = "open"

    config = EntityLifecycleConfig(
        entity_type="plain",
        valid_transitions={"open": ["closed"]},
    )
    svc = LifecycleService(configs={"plain": config})
    p = Plain()
    record = svc.transition("plain", p, "closed")
    assert p.status == "closed"
    assert record.from_status == "open"
    assert record.to_status == "closed"
