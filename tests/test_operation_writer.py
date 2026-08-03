"""Tests for OperationWriter — appending operations to Operations.md."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hermes.kernel.business_data_loader import BusinessDataLoader
from hermes.kernel.operation_id_bk import generate_bk_operation_id
from hermes.kernel.operation_writer import OperationWriter
from hermes.models.operation import Operation

# Use the real AVANZIA business dir as a template
_AVANZIA_DIR = Path("businesses/AVANZIA")


@pytest.fixture()
def business_dir(tmp_path):
    """Copy Operations.md into a temp directory for isolated tests."""
    src = _AVANZIA_DIR / "Operations.md"
    dest = tmp_path / "Operations.md"
    shutil.copy2(src, dest)
    return tmp_path


def _make_operation(
    op_id: str = "OP-20260803-001",
    request: str = "Test operation",
    status: str = "completed",
    outcome: str = "Delivered on time",
    outcome_classification: str = "success",
    decision_id: str | None = "DEC-007",
    recommendation_id: str | None = "rec_bot_001",
    review_id: str | None = "review_2026-08-01T00:00:00+00:00",
) -> Operation:
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    return Operation(
        id=op_id,
        workspace_id="AVANZIA",
        request=request,
        status=status,
        created_at=now,
        updated_at=now,
        outcome=outcome,
        outcome_classification=outcome_classification,
        decision_id=decision_id,
        recommendation_id=recommendation_id,
        review_id=review_id,
    )


# -- BK Operation ID generation -----------------------------------------------


def test_generate_bk_operation_id_empty_dir(tmp_path):
    """No Operations.md → OPS-001."""
    assert generate_bk_operation_id(tmp_path) == "OPS-001"


def test_generate_bk_operation_id_after_append(business_dir):
    """After appending OPS-001, next should be OPS-002."""
    writer = OperationWriter()
    writer.append_operation(business_dir, _make_operation(), "OPS-001")
    assert generate_bk_operation_id(business_dir) == "OPS-002"


# -- Operation Writer ----------------------------------------------------------


def test_append_creates_valid_table_row(business_dir):
    """Appended row should contain the operation ID and request."""
    writer = OperationWriter()
    writer.append_operation(business_dir, _make_operation(), "OPS-001")
    text = (business_dir / "Operations.md").read_text()
    assert "OPS-001" in text
    assert "Test operation" in text


def test_append_outcome_with_classification(business_dir):
    """Outcome should include classification prefix."""
    writer = OperationWriter()
    op = _make_operation(outcome="Done", outcome_classification="success")
    writer.append_operation(business_dir, op, "OPS-001")
    text = (business_dir / "Operations.md").read_text()
    assert "Success" in text


def test_append_completed_status(business_dir):
    """Completed status should render correctly."""
    writer = OperationWriter()
    writer.append_operation(business_dir, _make_operation(status="completed"), "OPS-001")
    text = (business_dir / "Operations.md").read_text()
    assert "Completed" in text


def test_append_failed_status(business_dir):
    """Failed status should render correctly."""
    writer = OperationWriter()
    op = _make_operation(status="failed", outcome_classification="failure")
    writer.append_operation(business_dir, op, "OPS-001")
    text = (business_dir / "Operations.md").read_text()
    assert "Failed" in text


def test_multiple_appends(business_dir):
    """Appending two operations should result in both present."""
    writer = OperationWriter()
    writer.append_operation(business_dir, _make_operation(request="First"), "OPS-001")
    writer.append_operation(business_dir, _make_operation(request="Second"), "OPS-002")
    text = (business_dir / "Operations.md").read_text()
    assert "OPS-001" in text
    assert "OPS-002" in text
    assert "First" in text
    assert "Second" in text


def test_append_missing_file_raises(tmp_path):
    """Appending to a missing Operations.md should raise FileNotFoundError."""
    writer = OperationWriter()
    with pytest.raises(FileNotFoundError):
        writer.append_operation(tmp_path, _make_operation(), "OPS-001")


def test_round_trip_append_then_parse(business_dir):
    """After appending, BusinessDataLoader should parse the new operation."""
    # Copy all required business files for the loader
    for f in _AVANZIA_DIR.iterdir():
        if f.suffix == ".md" and f.name != "Operations.md":
            shutil.copy2(f, business_dir / f.name)

    writer = OperationWriter()
    writer.append_operation(
        business_dir,
        _make_operation(request="Launch campaign"),
        "OPS-001",
    )

    loader = BusinessDataLoader()
    data = loader.load(business_dir)

    assert len(data.operations) == 1
    op = data.operations[0]
    assert op.id == "OPS-001"
    assert op.status == "completed"


def test_append_preserves_table_structure(business_dir):
    """The closing boundary line should still exist after append."""
    writer = OperationWriter()
    writer.append_operation(business_dir, _make_operation(), "OPS-001")
    text = (business_dir / "Operations.md").read_text()
    # Should still have section structure
    assert "## Operation Log" in text
    assert "## Review Cadence" in text


# -- Operation model traceability fields ----------------------------------------


def test_operation_has_traceability_fields():
    """Operation model includes decision_id, recommendation_id, review_id."""
    op = _make_operation()
    assert op.decision_id == "DEC-007"
    assert op.recommendation_id == "rec_bot_001"
    assert op.review_id == "review_2026-08-01T00:00:00+00:00"


def test_operation_traceability_fields_optional():
    """Traceability fields should be optional (None by default)."""
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    op = Operation(
        id="OP-20260803-001",
        workspace_id="TEST",
        request="Test",
        status="created",
        created_at=now,
        updated_at=now,
    )
    assert op.decision_id is None
    assert op.recommendation_id is None
    assert op.review_id is None
    assert op.outcome is None
    assert op.outcome_classification is None


def test_operation_outcome_classification_values():
    """Outcome classification should accept all valid values."""
    for cls in ("success", "partial", "failure", "cancelled"):
        op = _make_operation(outcome_classification=cls)
        assert op.outcome_classification == cls
