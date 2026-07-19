# KPI Specification

**Object:** KPI\
**Status:** Draft v1.0

## Purpose

A Key Performance Indicator (KPI) measures progress toward a Goal. KPIs
provide the quantitative inputs used by Hermes OS to evaluate business
health and drive recommendations.

------------------------------------------------------------------------

## Required Fields

  Field           Type      Required  Description
  --------------- -------- ---------- ------------------------------
  kpi_id          String       ✓      Unique identifier
  business_id     String       ✓      Parent Business
  goal_id         String       ✓      Related Goal
  name            String       ✓      KPI name
  unit            String       ✓      %, \$, count, etc.
  current_value   Number       ✓      Latest measured value
  target_value    Number       ✓      Desired value
  frequency       Enum         ✓      Daily, Weekly, Monthly
  owner           String       ✓      Accountable owner
  status          Enum         ✓      On Track, At Risk, Off Track

------------------------------------------------------------------------

## Relationships

-   Belongs to one Business
-   Supports one Goal
-   Referenced by Executive Briefs
-   Used by the Decision Engine

------------------------------------------------------------------------

## Lifecycle

Defined → Measured → Reviewed → Archived

------------------------------------------------------------------------

## Validation Rules

-   Every KPI must belong to a Goal.
-   Every KPI must have a measurable unit and target.
-   KPIs should be updated according to their reporting frequency.

------------------------------------------------------------------------

## Used By

-   Executive Brief Engine
-   Decision Engine
-   Dashboards
-   Weekly and Monthly Reviews
