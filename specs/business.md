# Business Specification

**Object:** Business\
**Status:** Draft v1.0

## Purpose

A Business is the top-level managed entity within Hermes OS. Every goal,
KPI, decision, opportunity, bottleneck, experiment, lesson, and
executive brief belongs to exactly one Business.

------------------------------------------------------------------------

## Required Fields

  Field             Type      Required  Description
  ----------------- -------- ---------- --------------------------------
  business_id       String       ✓      Unique identifier
  name              String       ✓      Business name
  mission           Text         ✓      Why the business exists
  north_star_goal   String       ✓      Primary long-term objective
  owner             String       ✓      Accountable owner
  status            Enum         ✓      Idea, Active, Paused, Archived
  created_at        Date         ✓      Creation date

------------------------------------------------------------------------

## Relationships

A Business contains:

-   Goals
-   KPIs
-   Strategies
-   Decisions
-   Bottlenecks
-   Opportunities
-   Experiments
-   Lessons Learned
-   Executive Briefs

------------------------------------------------------------------------

## Lifecycle

Idea → Active → Scaling → Mature → Archived

------------------------------------------------------------------------

## Validation Rules

-   Every Business must have at least one active Goal.
-   Every KPI belongs to one Business.
-   Every Executive Brief references exactly one Business.
-   Business IDs must be unique across Hermes OS.

------------------------------------------------------------------------

## Used By

-   Business Memory
-   Decision Engine
-   Executive Brief Engine
-   Dashboards
-   AI Agents
