# Lesson Learned Specification

**Object:** Lesson

**Status:** Draft v1.0

## Purpose

A Lesson captures knowledge gained from decisions, experiments,
projects, or day-to-day operations. Lessons help Hermes OS avoid
repeating mistakes, reinforce successful practices, and continuously
improve recommendations.

------------------------------------------------------------------------

## Required Fields

  Field            Type      Required  Description
  ---------------- -------- ---------- -------------------------------------------------
  lesson_id        String       ✓      Unique identifier
  business_id      String       ✓      Parent Business
  title            String       ✓      Short lesson title
  summary          Text         ✓      What was learned
  source           Enum         ✓      Decision, Experiment, Project, Incident, Review
  recommendation   Text         ✓      Recommended future action
  owner            String       ✓      Responsible owner
  date             Date         ✓      Date recorded

------------------------------------------------------------------------

## Relationships

A Lesson may reference:

-   Decision
-   Experiment
-   Strategy
-   Goal
-   KPI
-   Opportunity
-   Bottleneck

Every Lesson belongs to one Business.

------------------------------------------------------------------------

## Lifecycle

Captured → Reviewed → Applied → Archived

------------------------------------------------------------------------

## Validation Rules

-   Every Lesson must identify a clear learning.
-   Every Lesson should include an actionable recommendation.
-   Lessons originating from Experiments or Decisions should reference
    their source object.

------------------------------------------------------------------------

## Used By

-   Decision Engine
-   Executive Brief Engine
-   Business Memory
-   Continuous Improvement
