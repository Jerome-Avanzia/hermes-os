# Bottleneck Specification

**Object:** Bottleneck\
**Status:** Draft v1.0

## Purpose

A Bottleneck identifies a constraint that limits business performance.
Hermes OS uses bottlenecks to prioritize recommendations and focus
improvement efforts.

------------------------------------------------------------------------

## Required Fields

  ----------------------------------------------------------------------------
  Field           Type               Required       Description
  --------------- ------------ -------------------- --------------------------
  bottleneck_id   String                ✓           Unique identifier

  business_id     String                ✓           Parent Business

  title           String                ✓           Short description

  category        Enum                  ✓           Product, Marketing, Sales,
                                                    Operations, Finance,
                                                    Technology

  impact          Enum                  ✓           Low, Medium, High,
                                                    Critical

  owner           String                ✓           Responsible owner

  status          Enum                  ✓           Open, Mitigating, Resolved
  ----------------------------------------------------------------------------

------------------------------------------------------------------------

## Relationships

A Bottleneck may be linked to:

-   Strategy
-   Goal
-   KPI
-   Opportunity
-   Decision
-   Experiment
-   Lesson

------------------------------------------------------------------------

## Lifecycle

Identified → Analysed → Mitigating → Resolved → Archived

------------------------------------------------------------------------

## Validation Rules

-   Every Bottleneck belongs to one Business.
-   Critical bottlenecks should appear in the Executive Brief.
-   Resolved bottlenecks should reference the decision or experiment
    that resolved them.

------------------------------------------------------------------------

## Used By

-   Decision Engine
-   Executive Brief Engine
-   Weekly Reviews
-   Continuous Improvement
