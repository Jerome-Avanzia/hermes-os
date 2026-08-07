"""Validation Adapter — code validation execution for Hermes.

Architecture position:
  Execution Gateway → Validation Adapter → Subprocess (validator)

The Validation Adapter is NOT a validator. It translates Hermes execution
contracts (ExecutionRequest) into subprocess validation calls and translates
the results back into typed ValidationExecutionResults.

Language detection and validator selection are the adapter's sole
responsibility. The workflow layer passes only a workspace-relative path.
The adapter:
  1. Resolves the path against workspace_root (same security model as
     FilesystemAdapter — absolute paths and path traversal are rejected).
  2. Detects the language from the file extension.
  3. Selects the appropriate ValidatorKind.
  4. Executes the validator subprocess.
  5. Returns a fully typed ValidationExecutionResult.

Gateway invariant (from Sprint 59):
  The Execution Gateway is the ONLY dispatch boundary. The adapter is
  never called directly — only after a successful gateway.dispatch().

Security model:
  All paths are workspace-relative. Absolute paths and paths that resolve
  outside workspace_root are rejected before any subprocess is invoked.

Validator registry (AT-3 scope):
  .py  → ValidatorKind.PYTHON_SYNTAX  (sys.executable -m py_compile)
  *    → ValidatorKind.NONE           (pass unconditionally)

Architecture invariants preserved:
  - The adapter never plans work.
  - The adapter never performs routing.
  - The adapter never calls LLMs, Git, Docker, or HTTP.
  - The adapter never bypasses the Execution Gateway.
  - The adapter never raises exceptions — all failures are captured in
    ValidationExecutionResult(success=False, error=...).

Sources of non-determinism introduced (explicit):
  - Subprocess timing is non-deterministic (duration_ms varies).
  - All other logic is deterministic given the same file contents.

Filesystem writes introduced:
  - None. The adapter is read-only with respect to the filesystem.

Network calls introduced:
  - None.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

from hermes.models.execution_gateway import ExecutionAdapter, ExecutionRequest
from hermes.models.validation_adapter import (
    ValidationExecutionResult,
    ValidationRequest,
    ValidationResult,
    ValidatorKind,
)

logger = logging.getLogger(__name__)

# ── Language → ValidatorKind registry ─────────────────────────────────────────

_EXTENSION_TO_VALIDATOR: dict[str, ValidatorKind] = {
    ".py": ValidatorKind.PYTHON_SYNTAX,
    ".pyi": ValidatorKind.PYTHON_SYNTAX,
}


def _detect_validator(path: Path) -> tuple[str, ValidatorKind]:
    """Detect language and select ValidatorKind from file extension.

    Returns:
        Tuple of (language_detected, ValidatorKind).
        language_detected is a human-readable string for audit logging.
    """
    ext = path.suffix.lower()
    validator = _EXTENSION_TO_VALIDATOR.get(ext, ValidatorKind.NONE)
    language = "python" if validator == ValidatorKind.PYTHON_SYNTAX else "unknown"
    return language, validator


# ── ValidationAdapter ──────────────────────────────────────────────────────────


class ValidationAdapter:
    """Code validation execution adapter.

    Validates a workspace-relative file by detecting its language and
    dispatching to the appropriate validator subprocess. Returns a fully
    typed ValidationExecutionResult for every invocation.

    Construction::

        adapter = ValidationAdapter(workspace_root=Path("/data/repos"))
        result = adapter.execute(request)
        # result.success → True if file passed validation
        # result.validation_result.stderr → error detail on failure

    The adapter is language-agnostic at the interface level. Validator
    selection is driven entirely by file extension — the workflow layer
    passes only a path.
    """

    def __init__(self, workspace_root: Path) -> None:
        """Initialise the adapter with a workspace root.

        Args:
            workspace_root: Absolute path to the workspace root directory.
                            All request paths are resolved relative to this.
        """
        self._workspace_root = workspace_root.resolve()

    # ── Path resolution ────────────────────────────────────────────────────────

    def _resolve_path(self, path: str) -> Path | None:
        """Resolve a workspace-relative path and verify it stays inside root.

        Returns:
            Resolved Path if valid and inside workspace_root; None otherwise.
        """
        if not path or not path.strip():
            return None
        if Path(path).is_absolute():
            return None
        resolved = (self._workspace_root / path).resolve()
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError:
            return None
        return resolved

    # ── Validators ─────────────────────────────────────────────────────────────

    def _validate_python_syntax(self, resolved: Path) -> ValidationResult:
        """Run python -m py_compile against the target file.

        Uses sys.executable to guarantee the same Python interpreter that
        runs Hermes is used for validation — no PATH ambiguity.

        Args:
            resolved: Absolute path to the file to validate.

        Returns:
            ValidationResult with exit_code=0 on success; stderr contains
            the SyntaxError detail on failure.
        """
        start = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", str(resolved)],
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ValidationResult(
                success=False,
                validator_used=ValidatorKind.PYTHON_SYNTAX.value,
                language_detected="python",
                exit_code=-1,
                stdout="",
                stderr=f"subprocess_error: {type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        return ValidationResult(
            success=proc.returncode == 0,
            validator_used=ValidatorKind.PYTHON_SYNTAX.value,
            language_detected="python",
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_ms=duration_ms,
        )

    def _validate_none(self, resolved: Path, language: str) -> ValidationResult:
        """Pass unconditionally for languages with no registered validator.

        Args:
            resolved: Absolute path to the file (used for logging only).
            language: Human-readable language name for the audit trail.

        Returns:
            ValidationResult with success=True, exit_code=-1 (no process run).
        """
        return ValidationResult(
            success=True,
            validator_used=ValidatorKind.NONE.value,
            language_detected=language,
            exit_code=-1,
            stdout="",
            stderr="",
            duration_ms=0,
        )

    # ── Request builder ────────────────────────────────────────────────────────

    def build_validation_request(
        self, request: ExecutionRequest
    ) -> ValidationRequest | None:
        """Translate an ExecutionRequest payload into a ValidationRequest.

        Extracts "path" from the request payload. Returns None if the path
        key is absent.

        Args:
            request: The ExecutionRequest received from the Gateway.

        Returns:
            ValidationRequest if payload is valid; None otherwise.
        """
        payload_dict = dict(request.payload)
        path = payload_dict.get("path", "")
        if not path:
            return None

        resolved = self._resolve_path(path)
        if resolved is None:
            return None

        _, validator_kind = _detect_validator(resolved)
        return ValidationRequest(path=path, validator=validator_kind)

    # ── Execution ──────────────────────────────────────────────────────────────

    def execute(self, request: ExecutionRequest) -> ValidationExecutionResult:
        """Execute a validation request through the appropriate validator.

        Execution steps:
          1. Verify adapter_type is VALIDATION.
          2. Build ValidationRequest from payload (resolve path, detect language).
          3. Resolve path against workspace_root (security check).
          4. Detect language and select ValidatorKind.
          5. Dispatch to the appropriate validator.
          6. Return ValidationExecutionResult.

        The adapter never raises exceptions — all failures are captured in
        ValidationExecutionResult(success=False, error=...).

        Args:
            request: The ExecutionRequest from the Execution Gateway.

        Returns:
            ValidationExecutionResult capturing the full validation outcome.
        """
        meta_base: dict[str, str] = {"action": request.action_id}

        # ── 1. Verify adapter type ─────────────────────────────────────────
        if request.adapter_type != ExecutionAdapter.VALIDATION:
            error_msg = (
                f"adapter_type must be VALIDATION, got "
                f"{request.adapter_type.value!r}"
            )
            return ValidationExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                success=False,
                error=error_msg,
                validation_request=None,
                validation_result=None,
                adapter_metadata=tuple(sorted(meta_base.items())),
            )

        # ── 2. Extract and validate path from payload ──────────────────────
        payload_dict = dict(request.payload)
        path = payload_dict.get("path", "").strip()

        if not path:
            return ValidationExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                success=False,
                error="validation_request_invalid: payload missing 'path' key",
                validation_request=None,
                validation_result=None,
                adapter_metadata=tuple(sorted(meta_base.items())),
            )

        # ── 3. Resolve path (security check) ──────────────────────────────
        if Path(path).is_absolute():
            return ValidationExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                success=False,
                error=f"validation_path_invalid: absolute paths are not allowed: {path!r}",
                validation_request=None,
                validation_result=None,
                adapter_metadata=tuple(sorted(meta_base.items())),
            )

        resolved = (self._workspace_root / path).resolve()
        try:
            resolved.relative_to(self._workspace_root)
        except ValueError:
            return ValidationExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                success=False,
                error=f"validation_path_invalid: path escapes workspace root: {path!r}",
                validation_request=None,
                validation_result=None,
                adapter_metadata=tuple(sorted(meta_base.items())),
            )

        # ── 4. Detect language and select validator ────────────────────────
        language, validator_kind = _detect_validator(resolved)
        val_request = ValidationRequest(path=path, validator=validator_kind)

        meta_base["validator"] = validator_kind.value
        meta_base["language"] = language
        meta_base["path"] = path

        logger.info(
            "ValidationAdapter: validating path=%r language=%r validator=%r",
            path, language, validator_kind.value,
        )

        # ── 5. Dispatch to validator ───────────────────────────────────────
        try:
            if validator_kind == ValidatorKind.PYTHON_SYNTAX:
                val_result = self._validate_python_syntax(resolved)
            else:
                val_result = self._validate_none(resolved, language)
        except Exception as exc:
            error_msg = f"validation_dispatch_error: {type(exc).__name__}: {exc}"
            logger.warning(
                "ValidationAdapter: unexpected error validating %r: %s",
                path, error_msg,
            )
            return ValidationExecutionResult(
                request_id=request.request_id,
                operation_id=request.operation_id,
                success=False,
                error=error_msg,
                validation_request=val_request,
                validation_result=None,
                adapter_metadata=tuple(sorted(meta_base.items())),
            )

        # ── 6. Assemble result ─────────────────────────────────────────────
        success = val_result.success
        error: str | None = None
        if not success:
            error = f"validation_failed: {val_result.stderr.strip() or 'validator returned non-zero exit code'}"

        if success:
            logger.info(
                "ValidationAdapter: validation passed path=%r validator=%r duration_ms=%d",
                path, validator_kind.value, val_result.duration_ms,
            )
        else:
            logger.warning(
                "ValidationAdapter: validation failed path=%r validator=%r "
                "exit_code=%d stderr=%r",
                path, validator_kind.value, val_result.exit_code, val_result.stderr[:200],
            )

        meta_final = dict(meta_base)
        meta_final["exit_code"] = str(val_result.exit_code)
        meta_final["duration_ms"] = str(val_result.duration_ms)

        return ValidationExecutionResult(
            request_id=request.request_id,
            operation_id=request.operation_id,
            success=success,
            error=error,
            validation_request=val_request,
            validation_result=val_result,
            adapter_metadata=tuple(sorted(meta_final.items())),
        )
