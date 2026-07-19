# Executive Brief Specification

**Object:** Executive Brief\
**Status:** Draft v1.0

## Purpose

An Executive Brief is the primary decision-support output of Hermes OS.
It summarizes the current state of a business by consolidating
information from all core domain objects into a concise, actionable
briefing.

------------------------------------------------------------------------

## Required Fields

  Field              Type        Required  Description
  ------------------ ---------- ---------- -----------------------------------
  brief_id           String         ✓      Unique identifier
  business_id        String         ✓      Parent Business
  reporting_period   String         ✓      Daily, Weekly, Monthly, Quarterly
  generated_at       DateTime       ✓      Generation timestamp
  summary            Text           ✓      Overall business summary
  priorities         List           ✓      Top recommended priorities
  risks              List           ✓      Key risks and blockers
  recommendations    List           ✓      Suggested next actions
  status             Enum           ✓      Draft, Published, Archived

------------------------------------------------------------------------

## Inputs

An Executive Brief may aggregate data from:

-   Business
-   Strategy
-   Goal
-   KPI
-   Decision
-   Bottleneck
-   Opportunity
-   Experiment
-   Lesson

------------------------------------------------------------------------

## Lifecycle

Generated → Reviewed → Published → Archived

------------------------------------------------------------------------

## Validation Rules

-   Every Executive Brief belongs to one Business.
-   Every Brief should include at least one priority and one
    recommendation.
-   Published briefs should be immutable and retained for historical
    analysis.

------------------------------------------------------------------------

## Used By

-   Business Owners
-   Executive Dashboard
-   Decision Engine
-   Weekly, Monthly, and Quarterly Reviews
-   AI Agents for planning and prioritization
