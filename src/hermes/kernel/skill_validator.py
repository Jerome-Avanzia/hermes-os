"""SkillValidator — deterministic SkillManifest validation.

Sprint 53: Validates a SkillManifest against semantic rules.

The validator:
- Is deterministic: same inputs always produce the same SkillValidationResult.
- Has no side effects: no filesystem access, no provider calls, no state.
- Is stateless: each validate() call is independent.
- Returns a typed result: never raises for validation failures.

Validation checks:
  - id      — non-empty, valid slug (lowercase letters, digits, hyphens)
  - name    — non-empty
  - capabilities — at least one capability; no empty capability IDs
  - keywords — no empty entries
  - depends_on — no empty skill_ids; no self-dependency;
                  if available_skill_ids provided, all must be registered
  - execution — no empty adapter names (if execution is declared)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hermes.models.skill import SkillManifest

# Valid skill ID pattern: lowercase letters, digits, hyphens; starts with letter/digit
_SLUG_PATTERN: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# ── Validation types ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SkillValidationError:
    """A single validation issue in a skill manifest.

    Immutable after construction.
    """

    field: str       # which manifest field failed (e.g. "id", "capabilities")
    message: str     # what is wrong


@dataclass(frozen=True, slots=True)
class SkillValidationResult:
    """The outcome of validating a SkillManifest.

    valid is True only when errors is empty.
    Immutable after construction.
    """

    valid: bool
    errors: tuple[SkillValidationError, ...]


# ── SkillValidator ─────────────────────────────────────────────────────────────


class SkillValidator:
    """Deterministic validator for SkillManifest objects.

    Usage::

        validator = SkillValidator()
        result = validator.validate(manifest)
        if not result.valid:
            for error in result.errors:
                print(f"{error.field}: {error.message}")

    To validate cross-skill dependencies, provide available_skill_ids::

        result = validator.validate(manifest, available_skill_ids=frozenset({...}))

    The validator is stateless. It may be reused across calls and threads.
    """

    def validate(
        self,
        manifest: SkillManifest,
        available_skill_ids: frozenset[str] | None = None,
    ) -> SkillValidationResult:
        """Validate a SkillManifest.

        Args:
            manifest: The SkillManifest to validate.
            available_skill_ids: If provided, every skill_id in
                manifest.depends_on must be a member. Pass None to
                skip dependency availability checking.

        Returns:
            SkillValidationResult with valid=True if no errors were found.
            The errors tuple is empty when valid is True.
        """
        errors: list[SkillValidationError] = []

        self._validate_id(manifest, errors)
        self._validate_name(manifest, errors)
        self._validate_capabilities(manifest, errors)
        self._validate_keywords(manifest, errors)
        self._validate_dependencies(manifest, errors, available_skill_ids)
        self._validate_execution(manifest, errors)

        return SkillValidationResult(
            valid=len(errors) == 0,
            errors=tuple(errors),
        )

    # ── Field validators ──────────────────────────────────────────────────────

    @staticmethod
    def _validate_id(
        manifest: SkillManifest,
        errors: list[SkillValidationError],
    ) -> None:
        if not manifest.id:
            errors.append(SkillValidationError(
                field="id",
                message="Skill ID must not be empty",
            ))
            return
        if not _SLUG_PATTERN.match(manifest.id):
            errors.append(SkillValidationError(
                field="id",
                message=(
                    f"Skill ID {manifest.id!r} must contain only lowercase "
                    f"letters, digits, and hyphens, and must start with a "
                    f"letter or digit"
                ),
            ))

    @staticmethod
    def _validate_name(
        manifest: SkillManifest,
        errors: list[SkillValidationError],
    ) -> None:
        if not manifest.name.strip():
            errors.append(SkillValidationError(
                field="name",
                message="Skill name must not be empty",
            ))

    @staticmethod
    def _validate_capabilities(
        manifest: SkillManifest,
        errors: list[SkillValidationError],
    ) -> None:
        if not manifest.capabilities:
            errors.append(SkillValidationError(
                field="capabilities",
                message="Skill must declare at least one capability",
            ))
            return
        for cap in manifest.capabilities:
            if not cap.id.strip():
                errors.append(SkillValidationError(
                    field="capabilities",
                    message="Capability ID must not be empty",
                ))

    @staticmethod
    def _validate_keywords(
        manifest: SkillManifest,
        errors: list[SkillValidationError],
    ) -> None:
        for kw in manifest.keywords:
            if not kw.strip():
                errors.append(SkillValidationError(
                    field="keywords",
                    message="Keyword must not be empty or whitespace",
                ))

    @staticmethod
    def _validate_dependencies(
        manifest: SkillManifest,
        errors: list[SkillValidationError],
        available_skill_ids: frozenset[str] | None,
    ) -> None:
        for dep in manifest.depends_on:
            if not dep.skill_id.strip():
                errors.append(SkillValidationError(
                    field="depends_on",
                    message="Dependency skill_id must not be empty",
                ))
                continue
            if dep.skill_id == manifest.id:
                errors.append(SkillValidationError(
                    field="depends_on",
                    message=(
                        f"Skill {manifest.id!r} cannot declare a dependency "
                        f"on itself"
                    ),
                ))
                continue
            if (
                available_skill_ids is not None
                and dep.skill_id not in available_skill_ids
            ):
                errors.append(SkillValidationError(
                    field="depends_on",
                    message=(
                        f"Dependency {dep.skill_id!r} is not a registered skill. "
                        f"Available: {sorted(available_skill_ids)}"
                    ),
                ))

    @staticmethod
    def _validate_execution(
        manifest: SkillManifest,
        errors: list[SkillValidationError],
    ) -> None:
        if manifest.execution is None:
            return
        for adapter in manifest.execution.adapters:
            if not adapter.strip():
                errors.append(SkillValidationError(
                    field="execution.adapters",
                    message="Adapter name must not be empty or whitespace",
                ))
