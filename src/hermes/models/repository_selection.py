"""Repository Selection — typed contracts for autonomous target file selection.

Architecture position:
  EngineeringWorkflow (autonomous mode)
       ↓
  LlmAdapter.execute(action_id="propose_target")
       ↓
  RepositorySelectionResult  ←  this module
       ↓
  RepositoryManipulation gate
       ↓
  Deterministic pipeline (generate → write → validate → test → add → commit)

Phase 5 introduces autonomous target selection: instead of the Founder specifying
the output file via --output, Hermes proposes the target file from the repository
context, validates the proposal via RepositoryManipulation, then executes the
existing pipeline.

Two confidence levels:
  "high"      → Hermes identified exactly one correct target; execution proceeds
  "ambiguous" → multiple files are plausible; workflow halts with ordered candidates

No "low" confidence state — Hermes either commits or asks.

All contracts in this module are immutable after construction.
"""

from __future__ import annotations

from dataclasses import dataclass


# ── RepositorySelectionResult ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RepositorySelectionResult:
    """Immutable result of the LLM's propose_target operation.

    Produced by LlmAdapter when action_id="propose_target". Contains the LLM's
    proposal for the target file and operation. The EngineeringWorkflow validates
    this result via RepositoryManipulation before accepting it.

    selected_file → repo-relative path to the proposed target file.
                    Empty string ("") when confidence="ambiguous".
    operation     → "create_file" if the file does not yet exist;
                    "modify_file" if it already exists.
                    Empty string ("") when confidence="ambiguous".
    confidence    → "high" if Hermes is certain about exactly one target;
                    "ambiguous" if multiple files are equally plausible.
                    No other values are valid.
    basis         → one-sentence structured rationale for the selection.
                    Describes why this file was chosen or why the result is ambiguous.
    candidates    → alphabetically sorted tuple of plausible repo-relative paths.
                    Non-empty only when confidence="ambiguous"; empty when confidence="high".

    Immutable after construction.
    """

    selected_file: str           # repo-relative path; "" when ambiguous
    operation: str               # "create_file" | "modify_file" | "" when ambiguous
    confidence: str              # "high" | "ambiguous"
    basis: str                   # one-sentence structured rationale
    candidates: tuple[str, ...]  # alphabetically sorted; non-empty only when ambiguous


# ── SelectionExecutionResult ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SelectionExecutionResult:
    """Immutable outcome of a propose_target adapter execution.

    Returned by LlmAdapter.execute() when action_id="propose_target".
    Parallel to AdapterExecutionResult but carries a RepositorySelectionResult
    instead of an LLMResponse.

    Lifecycle states:
      success=True  → selection_result is populated; confidence is "high" or "ambiguous"
      success=False → error describes failure reason; selection_result is None
        - JSON parse failure, missing fields, invalid confidence, provider error

    request_id        → from the originating ExecutionRequest
    operation_id      → from the originating ExecutionRequest
    success           → True if the LLM responded and JSON was parsed successfully
    error             → error description if success=False; None otherwise
    selection_result  → the parsed RepositorySelectionResult; None on failure
    adapter_metadata  → sorted tuple of adapter-level key-value pairs

    Immutable after construction.
    """

    request_id: str
    operation_id: str
    success: bool
    error: str | None
    selection_result: RepositorySelectionResult | None
    adapter_metadata: tuple[tuple[str, str], ...]


# ── Public API ─────────────────────────────────────────────────────────────────


__all__ = [
    "RepositorySelectionResult",
    "SelectionExecutionResult",
]
