"""Tests for DecisionWriter — appending decisions to Decisions.md."""

import shutil
import tempfile
from pathlib import Path

import pytest

from hermes.kernel.business_data_loader import BusinessDataLoader
from hermes.kernel.decision_id import generate_decision_id
from hermes.kernel.decision_writer import DecisionWriter
from hermes.models.decision import Decision

# Use the real AVANZIA business dir as a template
_AVANZIA_DIR = Path("businesses/AVANZIA")


@pytest.fixture()
def business_dir(tmp_path):
    """Copy Decisions.md into a temp directory for isolated tests."""
    src = _AVANZIA_DIR / "Decisions.md"
    dest = tmp_path / "Decisions.md"
    shutil.copy2(src, dest)
    return tmp_path


def _make_decision(
    decision_id: str = "DEC-007",
    title: str = "Test decision",
    rationale: str = "Expected to improve outcomes.",
    status: str = "approved",
    decision_date: str = "2026-08",
    recommendation_id: str = "rec_bot_001",
    review_id: str = "review_2026-08-01T00:00:00+00:00",
    brief_id: str = "brief_AVANZIA_2026-08-01T00:00:00+00:00",
) -> Decision:
    return Decision(
        decision_id=decision_id,
        business_id="AVANZIA",
        title=title,
        context="Source: bottleneck/BOT-001. " + title,
        rationale=rationale,
        status=status,
        decision_date=decision_date,
        owner="Founder",
        recommendation_id=recommendation_id,
        review_id=review_id,
        brief_id=brief_id,
        created_at="2026-08-01T00:00:00+00:00",
        updated_at="2026-08-01T00:00:00+00:00",
    )


# -- Decision ID generation --------------------------------------------------


def test_generate_decision_id_from_avanzia():
    """Next ID after DEC-006 should be DEC-007."""
    assert generate_decision_id(_AVANZIA_DIR) == "DEC-007"


def test_generate_decision_id_empty_dir(tmp_path):
    """No Decisions.md → DEC-001."""
    assert generate_decision_id(tmp_path) == "DEC-001"


def test_generate_decision_id_after_append(business_dir):
    """After appending DEC-007, next should be DEC-008."""
    writer = DecisionWriter()
    writer.append_decision(business_dir, _make_decision())
    assert generate_decision_id(business_dir) == "DEC-008"


# -- Decision Writer ---------------------------------------------------------


def test_append_creates_valid_table_row(business_dir):
    """Appended row should contain the decision ID and title."""
    writer = DecisionWriter()
    writer.append_decision(business_dir, _make_decision())
    text = (business_dir / "Decisions.md").read_text()
    assert "DEC-007" in text
    assert "Test decision" in text


def test_append_preserves_existing_rows(business_dir):
    """Original 6 decisions should still be present after appending."""
    writer = DecisionWriter()
    writer.append_decision(business_dir, _make_decision())
    text = (business_dir / "Decisions.md").read_text()
    for i in range(1, 7):
        assert f"DEC-{i:03d}" in text


def test_append_decision_status_title_case(business_dir):
    """Status should be rendered in title case in the table."""
    writer = DecisionWriter()
    writer.append_decision(business_dir, _make_decision(status="approved"))
    text = (business_dir / "Decisions.md").read_text()
    assert "Approved" in text


def test_append_closed_status(business_dir):
    """Closed status should render correctly."""
    writer = DecisionWriter()
    writer.append_decision(business_dir, _make_decision(status="closed"))
    text = (business_dir / "Decisions.md").read_text()
    assert "Closed" in text


def test_append_proposed_status(business_dir):
    """Proposed (postponed) status should render correctly."""
    writer = DecisionWriter()
    writer.append_decision(business_dir, _make_decision(status="proposed"))
    text = (business_dir / "Decisions.md").read_text()
    assert "Proposed" in text


def test_round_trip_append_then_parse(business_dir):
    """After appending, BusinessDataLoader should parse the new decision."""
    # Copy all required business files for the loader
    for f in _AVANZIA_DIR.iterdir():
        if f.suffix == ".md" and f.name != "Decisions.md":
            shutil.copy2(f, business_dir / f.name)

    writer = DecisionWriter()
    writer.append_decision(business_dir, _make_decision(
        title="Invest in marketing",
        rationale="Increase brand awareness and client acquisition.",
    ))

    loader = BusinessDataLoader()
    data = loader.load(business_dir)

    # Should now have 7 decisions (6 original + 1 appended)
    assert len(data.decisions) == 7

    new_dec = [d for d in data.decisions if d.decision_id == "DEC-007"]
    assert len(new_dec) == 1
    assert new_dec[0].title == "Invest in marketing"
    assert new_dec[0].status == "approved"


def test_multiple_appends(business_dir):
    """Appending two decisions should result in both present."""
    writer = DecisionWriter()
    writer.append_decision(business_dir, _make_decision(
        decision_id="DEC-007", title="First",
    ))
    writer.append_decision(business_dir, _make_decision(
        decision_id="DEC-008", title="Second",
    ))
    text = (business_dir / "Decisions.md").read_text()
    assert "DEC-007" in text
    assert "DEC-008" in text
    assert "First" in text
    assert "Second" in text


def test_append_long_title_wraps(business_dir):
    """Long titles should wrap within the column width."""
    writer = DecisionWriter()
    long_title = "Implement a comprehensive knowledge management system for all portfolio companies"
    writer.append_decision(business_dir, _make_decision(title=long_title))
    text = (business_dir / "Decisions.md").read_text()
    assert "DEC-007" in text
    # The full title should be reconstructable from the wrapped lines
    assert "knowledge" in text.lower()


def test_append_missing_file_raises(tmp_path):
    """Appending to a missing Decisions.md should raise FileNotFoundError."""
    writer = DecisionWriter()
    with pytest.raises(FileNotFoundError):
        writer.append_decision(tmp_path, _make_decision())


# -- Decision model traceability fields --------------------------------------


def test_decision_has_traceability_fields():
    """Decision model includes recommendation_id, review_id, brief_id."""
    d = _make_decision()
    assert d.recommendation_id == "rec_bot_001"
    assert d.review_id == "review_2026-08-01T00:00:00+00:00"
    assert d.brief_id == "brief_AVANZIA_2026-08-01T00:00:00+00:00"


def test_decision_traceability_fields_optional():
    """Traceability fields should be optional (None by default)."""
    d = Decision(
        decision_id="DEC-001",
        business_id="TEST",
        title="Test",
        context="Test",
        rationale="Test",
        status="proposed",
        decision_date="2026-08",
    )
    assert d.recommendation_id is None
    assert d.review_id is None
    assert d.brief_id is None
