# Decision Specification

**Object:** Decision\
**Status:** Draft v1.0

## Purpose

A Decision records a significant business choice, the reasoning behind
it, and its outcome. Decisions create an auditable history that Hermes
OS can reference when making future recommendations.

------------------------------------------------------------------------

## Required Fields

  Field         Type      Required  Description
  ------------- -------- ---------- -------------------------------------------
  decision_id   String       ✓      Unique identifier
  business_id   String       ✓      Parent Business
  title         String       ✓      Short decision name
  context       Text         ✓      Situation requiring a decision
  rationale     Text         ✓      Why this option was chosen
  owner         String       ✓      Decision owner
  date          Date         ✓      Decision date
  status        Enum         ✓      Proposed, Approved, Implemented, Reversed

------------------------------------------------------------------------

## Relationships

A Decision may relate to:

-   Business
-   Strategy
-   Goal
-   KPI
-   Opportunity
-   Bottleneck
-   Experiment
-   Lesson Learned

------------------------------------------------------------------------

## Lifecycle

Proposed → Approved → Implemented → Reviewed → Archived

------------------------------------------------------------------------

## Validation Rules

-   Every Decision belongs to one Business.
-   Implemented decisions should be reviewed for outcomes.
-   Significant decisions should reference the strategy or goal they
    support.

------------------------------------------------------------------------

## Used By

-   Decision Engine
-   Executive Brief Engine
-   Lessons Learned
-   Business Memory
