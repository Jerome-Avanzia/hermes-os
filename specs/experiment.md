# Experiment Specification

**Object:** Experiment\
**Status:** Draft v1.0

## Purpose

An Experiment captures a structured test designed to validate a
hypothesis before committing significant time or resources. Hermes OS
uses experiments to support evidence-based decision making and
continuous improvement.

------------------------------------------------------------------------

## Required Fields

  Field              Type      Required  Description
  ------------------ -------- ---------- ----------------------------------------
  experiment_id      String       ✓      Unique identifier
  business_id        String       ✓      Parent Business
  title              String       ✓      Experiment name
  hypothesis         Text         ✓      Statement being tested
  success_criteria   Text         ✓      Measurable definition of success
  owner              String       ✓      Responsible owner
  start_date         Date         ✓      Experiment start
  end_date           Date                Planned completion
  outcome            Enum                Success, Failure, Inconclusive
  status             Enum         ✓      Planned, Running, Completed, Cancelled

------------------------------------------------------------------------

## Relationships

An Experiment may relate to:

-   Strategy
-   Goal
-   KPI
-   Opportunity
-   Bottleneck
-   Decision
-   Lesson

------------------------------------------------------------------------

## Lifecycle

Planned → Running → Completed → Reviewed → Archived

------------------------------------------------------------------------

## Validation Rules

-   Every Experiment belongs to one Business.
-   Every Experiment must define a clear hypothesis and success
    criteria.
-   Completed experiments should produce at least one Lesson Learned.

------------------------------------------------------------------------

## Used By

-   Decision Engine
-   Executive Brief Engine
-   Continuous Improvement
-   Innovation Pipeline
