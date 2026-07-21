# Scoring Model

**Document:** Scoring Model\
**Status:** Draft v1.0

## Purpose

The Scoring Model defines how Hermes OS evaluates and ranks
opportunities, bottlenecks, and recommended actions. It ensures that
recommendations are consistent, transparent, and explainable.

------------------------------------------------------------------------

## Scoring Dimensions

Each candidate action is evaluated on a normalized scale from **1
(lowest)** to **5 (highest)**.

  -----------------------------------------------------------------------
  Dimension                        Description
  -------------------------------- --------------------------------------
  Strategic Alignment              Supports the active strategy and
                                   business goals

  Expected Impact                  Potential business value if successful

  Required Effort                  Resources, time, and complexity
                                   required

  Risk                             Likelihood and severity of failure

  Urgency                          Time sensitivity of taking action

  Confidence                       Quality of supporting evidence

  Historical Success               Similar actions that have succeeded
                                   previously
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## Default Weights

  Dimension               Weight
  --------------------- --------
  Strategic Alignment        25%
  Expected Impact            25%
  Required Effort            15%
  Risk                       10%
  Urgency                    10%
  Confidence                 10%
  Historical Success          5%

Weights may be customized per business while maintaining a total of
100%.

------------------------------------------------------------------------

## Overall Priority Score

The Decision Engine computes a weighted priority score using all
dimensions.

Higher scores indicate higher implementation priority.

------------------------------------------------------------------------

## Design Principles

-   Every recommendation must include its score.
-   Scores must be reproducible from stored data.
-   Weighting should remain configurable.
-   Low confidence should encourage experimentation rather than
    immediate implementation.

------------------------------------------------------------------------

## Used By

-   Decision Engine
-   Executive Brief Engine
-   Dashboards
-   Agent Orchestration
-   Opportunity Prioritization
