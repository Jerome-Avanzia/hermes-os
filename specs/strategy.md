# Strategy Specification

**Object:** Strategy\
**Status:** Draft v1.0

## Purpose

A Strategy describes the approach a Business will take to achieve one or
more Goals. It translates vision into coordinated initiatives and guides
decisions, priorities, and resource allocation.

------------------------------------------------------------------------

## Required Fields

  Field              Type      Required  Description
  ------------------ -------- ---------- -----------------------------------
  strategy_id        String       ✓      Unique identifier
  business_id        String       ✓      Parent Business
  title              String       ✓      Strategy name
  objective          Text         ✓      Desired business outcome
  owner              String       ✓      Accountable owner
  status             Enum         ✓      Draft, Active, Completed, Retired
  review_frequency   Enum         ✓      Monthly, Quarterly, Annually

------------------------------------------------------------------------

## Relationships

A Strategy may reference:

-   One or more Goals
-   Multiple KPIs
-   Decisions
-   Opportunities
-   Experiments

Every Strategy belongs to one Business.

------------------------------------------------------------------------

## Lifecycle

Draft → Active → Reviewed → Completed / Retired

------------------------------------------------------------------------

## Validation Rules

-   Every Strategy must belong to a Business.
-   Every active Strategy should support at least one Goal.
-   Strategies should be reviewed according to their review frequency.

------------------------------------------------------------------------

## Used By

-   Decision Engine
-   Executive Brief Engine
-   Quarterly Strategy Reviews
-   Business Dashboards
