# Goal Specification

**Object:** Goal\
**Status:** Draft v1.0

## Purpose

A Goal defines a measurable business outcome that Hermes OS helps
achieve. Every Goal belongs to exactly one Business and should be
supported by KPIs, strategies, decisions, and experiments.

------------------------------------------------------------------------

## Required Fields

  Field          Type      Required  Description
  -------------- -------- ---------- --------------------------------------
  goal_id        String       ✓      Unique identifier
  business_id    String       ✓      Parent Business
  title          String       ✓      Goal name
  description    Text         ✓      Business outcome
  target_value   String       ✓      Target to achieve
  target_date    Date         ✓      Due date
  owner          String       ✓      Accountable owner
  status         Enum         ✓      Planned, Active, Achieved, Cancelled

------------------------------------------------------------------------

## Relationships

A Goal may have:

-   Multiple KPIs
-   Multiple Strategies
-   Multiple Decisions
-   Multiple Experiments

Every Goal belongs to one Business.

------------------------------------------------------------------------

## Lifecycle

Planned → Active → Achieved → Archived

------------------------------------------------------------------------

## Validation Rules

-   Every Goal must belong to a Business.
-   Every active Goal should have at least one KPI.
-   Goals should have a measurable target and review date.

------------------------------------------------------------------------

## Used By

-   Decision Engine
-   Executive Brief Engine
-   KPI Dashboard
-   Strategy Reviews
