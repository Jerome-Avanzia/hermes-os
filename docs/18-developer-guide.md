# Developer Guide

**Document:** Developer Guide\
**Status:** Draft v1.0

## Purpose

This guide explains how developers contribute to Hermes OS while keeping
the architecture, specifications, and implementation artifacts
synchronized.

------------------------------------------------------------------------

## Repository Structure

``` text
docs/         Architecture and design
specs/        Human-readable business object specifications
contracts/    Canonical JSON Schemas
api/          API definitions (OpenAPI)
sdk/          Generated language SDKs
tests/        Validation and integration tests
examples/     Sample payloads and workflows
tools/        Build and generation scripts
```

------------------------------------------------------------------------

## Development Workflow

1.  Update the business specification (`specs/`) if the business concept
    changes.
2.  Update the corresponding JSON Schema (`contracts/`).
3.  Regenerate SDKs and API models.
4.  Add or update examples.
5.  Add validation tests.
6.  Commit all related changes together.

------------------------------------------------------------------------

## Design Rules

-   JSON Schema is the canonical machine-readable definition.
-   Do not manually edit generated SDKs.
-   Prefer automation over duplication.
-   Preserve backward compatibility whenever possible.
-   Document breaking changes.

------------------------------------------------------------------------

## Pull Request Checklist

-   [ ] Architecture updated (if needed)
-   [ ] Specification updated
-   [ ] Contract updated
-   [ ] Tests added or updated
-   [ ] Examples updated
-   [ ] Documentation reviewed

------------------------------------------------------------------------

## Future Automation

Hermes OS should automate:

-   Schema validation
-   SDK generation
-   OpenAPI generation
-   Documentation generation
-   Contract compatibility checks
-   CI quality gates
