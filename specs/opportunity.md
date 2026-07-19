# Opportunity Specification

**Object:** Opportunity\
**Status:** Draft v1.0

## Purpose

An Opportunity represents a potential initiative that could improve
business performance. Hermes OS evaluates opportunities by expected
impact, effort, strategic alignment, and urgency.

------------------------------------------------------------------------

## Required Fields

  Field              Type      Required  Description
  ------------------ -------- ---------- -----------------------------------------------
  opportunity_id     String       ✓      Unique identifier
  business_id        String       ✓      Parent Business
  title              String       ✓      Opportunity name
  description        Text         ✓      Summary of the opportunity
  expected_impact    Enum         ✓      Low, Medium, High
  estimated_effort   Enum         ✓      Low, Medium, High
  owner              String       ✓      Responsible owner
  status             Enum         ✓      Backlog, Planned, Active, Completed, Rejected

------------------------------------------------------------------------

## Relationships

An Opportunity may relate to:

-   Strategy
-   Goal
-   KPI
-   Decision
-   Bottleneck
-   Experiment
-   Lesson

------------------------------------------------------------------------

## Lifecycle

Identified → Evaluated → Planned → Active → Completed / Rejected

------------------------------------------------------------------------

## Validation Rules

-   Every Opportunity belongs to one Business.
-   Active opportunities should support at least one Goal or Strategy.
-   Completed opportunities should reference the resulting Decision or
    Experiment.

------------------------------------------------------------------------

## Used By

-   Decision Engine
-   Executive Brief Engine
-   Strategic Planning
-   Opportunity Backlog
