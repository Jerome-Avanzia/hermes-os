# ADR-0001 — Canonical Hermes Workspace Shell

Status: Accepted

Date: 2026-08-01

## Context

The repository contains a reference image:

docs/ui/hermes-workspace-v0.19.0-pilot.png

The repository contains no prior decision establishing whether this Pilot is:

- the canonical Workspace shell, or
- an exploratory prototype.

This ambiguity blocks implementation.

## Decision

The Founder designates Hermes Workspace v0.19.0 Pilot as the canonical Workspace shell for Hermes OS.

The Pilot is the authoritative UI baseline.

Future implementation shall recover and extend this Workspace rather than replacing or redesigning it, unless a later ADR explicitly supersedes this decision.

## Consequences

- The Pilot becomes an architectural artifact.
- UI implementation shall be measured against the Pilot.
- Recovery is the default implementation strategy.
- Deferred functionality remains deferred until explicitly scheduled.
- This ADR establishes the UI baseline only. It does not approve or reject the implementation of any individual module shown in the Pilot.

## References

- docs/ui/hermes-workspace-v0.19.0-pilot.png
