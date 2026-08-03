"""OperationRuntime — SOP step tracking for Operations.

Parses SOP markdown into structured steps, tracks per-step completion
state in Operation.extra_fields, and computes progress metrics.

Step IDs are slug-based (e.g. ``step-draft-review``) for stability
when steps are inserted or reordered in the SOP markdown.

Execution is sequential by default — only the next incomplete step
is actionable.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from hermes.models.operation import Operation
from hermes.models.operation_progress import OperationProgress
from hermes.models.sop import SOP
from hermes.models.sop_step import SOPStep

# Matches numbered list items like: 1. **Title** — Description
_STEP_RE = re.compile(
    r"^\d+\.\s+\*\*(.+?)\*\*(?:\s*[—–-]\s*(.*))?$"
)

_SOP_PROGRESS_KEY = "sop_progress"


class StepNotFoundError(Exception):
    pass


class StepNotActionableError(Exception):
    pass


def _slugify(text: str) -> str:
    """Convert a title to a stable slug ID."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return f"step-{slug}"


def parse_steps(sop: SOP) -> list[SOPStep]:
    """Parse the ## Steps section of an SOP into SOPStep objects."""
    lines = sop.content.split("\n")
    in_steps = False
    steps: list[SOPStep] = []
    seen_ids: set[str] = set()

    for line in lines:
        stripped = line.strip()

        # Detect start of Steps section
        if stripped.startswith("## Steps"):
            in_steps = True
            continue

        # Stop at next heading
        if in_steps and stripped.startswith("## "):
            break

        if not in_steps:
            continue

        m = _STEP_RE.match(stripped)
        if m:
            title = m.group(1).strip()
            description = (m.group(2) or "").strip()
            step_id = _slugify(title)

            # Ensure uniqueness
            base_id = step_id
            counter = 2
            while step_id in seen_ids:
                step_id = f"{base_id}-{counter}"
                counter += 1
            seen_ids.add(step_id)

            steps.append(SOPStep(
                id=step_id,
                index=len(steps),
                title=title,
                description=description,
            ))

    return steps


def get_progress(operation: Operation, sop: SOP) -> OperationProgress:
    """Compute progress by merging SOP steps with persisted state."""
    steps = parse_steps(sop)
    progress_data = operation.extra_fields.get(_SOP_PROGRESS_KEY, {})
    step_states = progress_data.get("steps", {})

    for step in steps:
        state = step_states.get(step.id, {})
        step.completed = state.get("completed", False)
        step.completed_at = state.get("completed_at")

    completed_count = sum(1 for s in steps if s.completed)
    total = len(steps)
    pct = round(completed_count * 100 / total) if total > 0 else 0
    all_complete = total > 0 and completed_count == total

    # Current step is the first incomplete step (sequential)
    current_step = None
    for s in steps:
        if not s.completed:
            current_step = s.id
            break

    return OperationProgress(
        sop_id=sop.id,
        total_steps=total,
        completed_steps=completed_count,
        completion_pct=pct,
        steps=steps,
        all_complete=all_complete,
        current_step=current_step,
    )


def complete_step(
    operation: Operation, sop: SOP, step_id: str,
) -> OperationProgress:
    """Mark a step complete and persist to operation.extra_fields.

    Sequential enforcement: only the current (next incomplete) step
    can be completed. Raises StepNotActionableError otherwise.
    """
    steps = parse_steps(sop)
    step_ids = {s.id for s in steps}

    if step_id not in step_ids:
        raise StepNotFoundError(f"Step not found: {step_id}")

    # Load existing state
    progress_data = operation.extra_fields.get(_SOP_PROGRESS_KEY, {})
    step_states = progress_data.get("steps", {})

    # Check already completed
    if step_states.get(step_id, {}).get("completed", False):
        return get_progress(operation, sop)

    # Sequential enforcement: find the current step
    current_step_id = None
    for s in steps:
        state = step_states.get(s.id, {})
        if not state.get("completed", False):
            current_step_id = s.id
            break

    if step_id != current_step_id:
        raise StepNotActionableError(
            f"Step '{step_id}' is not actionable. "
            f"Current step is '{current_step_id}'."
        )

    # Mark complete
    now = datetime.now(timezone.utc).isoformat()
    step_states[step_id] = {"completed": True, "completed_at": now}

    # Persist back
    operation.extra_fields[_SOP_PROGRESS_KEY] = {
        "sop_id": sop.id,
        "steps": step_states,
    }
    operation.updated_at = datetime.now(timezone.utc)

    return get_progress(operation, sop)
