"""Validation Adapter — typed contracts for the Validation Adapter.

Architecture position:
  Execution Gateway → Validation Adapter → Subprocess (validator)

The Validation Adapter is NOT a validator. It is an execution adapter that
translates Hermes execution contracts into subprocess validation calls and
translates the results back into typed Hermes results.

  Dispatch contract vs execution (invariant from Sprint 59):
    The Gateway determines which adapter receives the request and whether
    dispatch is valid. The Validation Adapter performs execution outside
    the deterministic Hermes kernel.

  Language detection and validator selection are entirely the adapter's
  responsibility. The workflow layer passes only a workspace-relative path.
  The adapter inspects the file extension, selects the appropriate
  ValidatorKind, and executes it.

  ValidatorKind.NONE is used when no validator is registered for the
  detected language. The adapter reports success with exit_code=0 and
  validator_used="none" — the gate never blocks on unknown languages.

  Every validation request follows this path:
    Execution Gateway → Validation Adapter → subprocess (e.g. py_compile)

Sprint AT-3 scope:
  One validator supported: python_syntax (python -m py_compile).
  Non-Python files use ValidatorKind.NONE and always pass.

All contracts in this module are immutable after construction.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


# ── ValidatorKind ──────────────────────────────────────────────────────────────


class ValidatorKind(enum.Enum):
    """Enumeration of supported validators.

    Each value identifies a specific validation strategy. The Validation
    Adapter maps file language to ValidatorKind internally — the workflow
    layer never specifies a validator directly.

    PYTHON_SYNTAX → runs `sys.executable -m py_compile <path>`
                    Fails on any SyntaxError. Fast, zero test-suite dependency.
    NONE          → no validator registered for the detected language.
                    Always succeeds. Used for unknown or non-Python files.
    """

    PYTHON_SYNTAX = "python_syntax"
    NONE = "none"


# ── ValidationRequest ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ValidationRequest:
    """Typed request for a single validation operation.

    Produced by ValidationAdapter.build_validation_request() from an
    ExecutionRequest. The adapter resolves the workspace-relative path
    and selects the validator before execution.

    path      → workspace-relative path as received from the workflow
    validator → ValidatorKind selected by the adapter based on language detection
    """

    path: str
    validator: ValidatorKind


# ── ValidationResult ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Typed result of a single validation operation.

    Returned by the internal validator methods in ValidationAdapter. Carries
    the complete subprocess outcome for inclusion in ValidationExecutionResult.

    success           → True if the validator reported no errors
    validator_used    → ValidatorKind value that was dispatched
    language_detected → e.g. "python", "unknown"
    exit_code         → subprocess exit code (0 = success); -1 for NONE validator
    stdout            → captured stdout from the validator process
    stderr            → captured stderr (contains error details on failure)
    duration_ms       → wall-clock time for the validation subprocess, in milliseconds
    """

    success: bool
    validator_used: str
    language_detected: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


# ── ValidationExecutionResult ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ValidationExecutionResult:
    """Complete outcome of a ValidationAdapter.execute() invocation.

    Parallel to FilesystemExecutionResult and AdapterExecutionResult:
    carries the full audit trail including request, result, and metadata.

    request_id          → mirrors the ExecutionRequest.request_id
    operation_id        → mirrors the ExecutionRequest.operation_id
    success             → True if validation passed (or NONE validator used)
    error               → human-readable error string if success=False; None otherwise
    validation_request  → the typed request built from ExecutionRequest payload
    validation_result   → the typed result from the validator; None if request build failed
    adapter_metadata    → sorted tuple of (key, value) pairs for audit/logging
    """

    request_id: str
    operation_id: str
    success: bool
    error: str | None
    validation_request: ValidationRequest | None
    validation_result: ValidationResult | None
    adapter_metadata: tuple[tuple[str, str], ...]
