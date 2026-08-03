"""Tests for OperationRuntime — SOP step parsing, progress tracking, and completion."""

from datetime import datetime, timezone

import pytest

from hermes.kernel.operation_runtime import (
    StepNotActionableError,
    StepNotFoundError,
    _slugify,
    complete_step,
    get_progress,
    parse_steps,
)
from hermes.models.operation import Operation
from hermes.models.sop import SOP


def _make_sop(content: str, sop_id: str = "test/sop") -> SOP:
    return SOP(
        id=sop_id,
        title="Test SOP",
        skill_id="test",
        filename="sop.md",
        content=content,
    )


def _make_operation(workspace_id: str = "ws1", sop_id: str | None = None) -> Operation:
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    return Operation(
        id="OPS-001",
        workspace_id=workspace_id,
        request="Test request",
        status="executing",
        created_at=now,
        updated_at=now,
        sop_id=sop_id,
    )


SOP_CONTENT = """\
# Content Review SOP

Overview text here.

## Steps

1. **Draft Review** — Review the initial draft for accuracy
2. **Fact Check** — Verify all claims and statistics
3. **Final Approval** — Sign off on the finished piece

## Notes

Some notes here.
"""


class TestSlugify:
    def test_simple_title(self):
        assert _slugify("Draft Review") == "step-draft-review"

    def test_special_characters(self):
        assert _slugify("Fact-Check & Verify") == "step-fact-check-verify"

    def test_leading_trailing_whitespace(self):
        assert _slugify("  Hello World  ") == "step-hello-world"

    def test_multiple_spaces(self):
        assert _slugify("A   B   C") == "step-a-b-c"

    def test_numbers(self):
        assert _slugify("Step 1 Review") == "step-step-1-review"


class TestParseSteps:
    def test_parses_three_steps(self):
        sop = _make_sop(SOP_CONTENT)
        steps = parse_steps(sop)
        assert len(steps) == 3

    def test_step_ids_are_slugs(self):
        sop = _make_sop(SOP_CONTENT)
        steps = parse_steps(sop)
        assert steps[0].id == "step-draft-review"
        assert steps[1].id == "step-fact-check"
        assert steps[2].id == "step-final-approval"

    def test_step_titles(self):
        sop = _make_sop(SOP_CONTENT)
        steps = parse_steps(sop)
        assert steps[0].title == "Draft Review"
        assert steps[1].title == "Fact Check"
        assert steps[2].title == "Final Approval"

    def test_step_descriptions(self):
        sop = _make_sop(SOP_CONTENT)
        steps = parse_steps(sop)
        assert steps[0].description == "Review the initial draft for accuracy"
        assert steps[1].description == "Verify all claims and statistics"

    def test_step_indices(self):
        sop = _make_sop(SOP_CONTENT)
        steps = parse_steps(sop)
        assert [s.index for s in steps] == [0, 1, 2]

    def test_steps_default_not_completed(self):
        sop = _make_sop(SOP_CONTENT)
        steps = parse_steps(sop)
        assert all(not s.completed for s in steps)

    def test_no_steps_section(self):
        sop = _make_sop("# Title\n\nJust a description.")
        steps = parse_steps(sop)
        assert steps == []

    def test_empty_steps_section(self):
        sop = _make_sop("# Title\n\n## Steps\n\n## Notes\n")
        steps = parse_steps(sop)
        assert steps == []

    def test_step_without_description(self):
        content = "## Steps\n\n1. **Review Only**\n"
        sop = _make_sop(content)
        steps = parse_steps(sop)
        assert len(steps) == 1
        assert steps[0].title == "Review Only"
        assert steps[0].description == ""

    def test_duplicate_titles_get_unique_ids(self):
        content = "## Steps\n\n1. **Review** — First\n2. **Review** — Second\n"
        sop = _make_sop(content)
        steps = parse_steps(sop)
        assert len(steps) == 2
        assert steps[0].id == "step-review"
        assert steps[1].id == "step-review-2"

    def test_stops_at_next_heading(self):
        content = "## Steps\n\n1. **A** — First\n\n## Other\n\n1. **B** — Ignored\n"
        sop = _make_sop(content)
        steps = parse_steps(sop)
        assert len(steps) == 1
        assert steps[0].title == "A"

    def test_en_dash_separator(self):
        content = "## Steps\n\n1. **Title** – description with en-dash\n"
        sop = _make_sop(content)
        steps = parse_steps(sop)
        assert steps[0].description == "description with en-dash"

    def test_hyphen_separator(self):
        content = "## Steps\n\n1. **Title** - description with hyphen\n"
        sop = _make_sop(content)
        steps = parse_steps(sop)
        assert steps[0].description == "description with hyphen"


class TestGetProgress:
    def test_fresh_operation_all_incomplete(self):
        sop = _make_sop(SOP_CONTENT)
        op = _make_operation()
        progress = get_progress(op, sop)
        assert progress.total_steps == 3
        assert progress.completed_steps == 0
        assert progress.completion_pct == 0
        assert not progress.all_complete
        assert progress.current_step == "step-draft-review"

    def test_with_persisted_state(self):
        sop = _make_sop(SOP_CONTENT)
        op = _make_operation()
        op.extra_fields["sop_progress"] = {
            "sop_id": "test/sop",
            "steps": {
                "step-draft-review": {"completed": True, "completed_at": "2026-08-03T00:00:00+00:00"},
            },
        }
        progress = get_progress(op, sop)
        assert progress.completed_steps == 1
        assert progress.completion_pct == 33
        assert progress.current_step == "step-fact-check"

    def test_all_complete(self):
        sop = _make_sop(SOP_CONTENT)
        op = _make_operation()
        op.extra_fields["sop_progress"] = {
            "sop_id": "test/sop",
            "steps": {
                "step-draft-review": {"completed": True, "completed_at": "t1"},
                "step-fact-check": {"completed": True, "completed_at": "t2"},
                "step-final-approval": {"completed": True, "completed_at": "t3"},
            },
        }
        progress = get_progress(op, sop)
        assert progress.all_complete
        assert progress.completion_pct == 100
        assert progress.current_step is None

    def test_no_steps_zero_percent(self):
        sop = _make_sop("# Title\n\nNo steps here.")
        op = _make_operation()
        progress = get_progress(op, sop)
        assert progress.total_steps == 0
        assert progress.completion_pct == 0
        assert progress.current_step is None


class TestCompleteStep:
    def test_complete_first_step(self):
        sop = _make_sop(SOP_CONTENT)
        op = _make_operation()
        progress = complete_step(op, sop, "step-draft-review")
        assert progress.completed_steps == 1
        assert progress.current_step == "step-fact-check"
        # Verify persisted state
        state = op.extra_fields["sop_progress"]["steps"]["step-draft-review"]
        assert state["completed"] is True
        assert state["completed_at"] is not None

    def test_sequential_enforcement(self):
        sop = _make_sop(SOP_CONTENT)
        op = _make_operation()
        with pytest.raises(StepNotActionableError, match="not actionable"):
            complete_step(op, sop, "step-fact-check")

    def test_complete_all_sequentially(self):
        sop = _make_sop(SOP_CONTENT)
        op = _make_operation()
        complete_step(op, sop, "step-draft-review")
        complete_step(op, sop, "step-fact-check")
        progress = complete_step(op, sop, "step-final-approval")
        assert progress.all_complete
        assert progress.current_step is None

    def test_step_not_found(self):
        sop = _make_sop(SOP_CONTENT)
        op = _make_operation()
        with pytest.raises(StepNotFoundError, match="nonexistent"):
            complete_step(op, sop, "nonexistent")

    def test_already_completed_is_noop(self):
        sop = _make_sop(SOP_CONTENT)
        op = _make_operation()
        p1 = complete_step(op, sop, "step-draft-review")
        p2 = complete_step(op, sop, "step-draft-review")
        assert p1.completed_steps == p2.completed_steps

    def test_updates_operation_timestamp(self):
        sop = _make_sop(SOP_CONTENT)
        op = _make_operation()
        original = op.updated_at
        complete_step(op, sop, "step-draft-review")
        assert op.updated_at >= original

    def test_persists_sop_id_in_progress(self):
        sop = _make_sop(SOP_CONTENT, sop_id="copywriting/content-review")
        op = _make_operation()
        complete_step(op, sop, "step-draft-review")
        assert op.extra_fields["sop_progress"]["sop_id"] == "copywriting/content-review"
