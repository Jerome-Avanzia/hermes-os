# Business JSON Schema Mapping

**Document:** Contract Mapping\
**Status:** Draft v1.0

## Purpose

This document maps each Hermes OS business object to its implementation
artifacts. It provides the bridge between the conceptual specifications
in `specs/` and machine-readable schemas in `contracts/`.

------------------------------------------------------------------------

## Mapping

  -------------------------------------------------------------------------------------------------------------
  Business Object     JSON Schema                   TypeScript          Python               Database
  ------------------- ----------------------------- ------------------- -------------------- ------------------
  Business            business.schema.json          Business.ts         business.py          businesses

  Strategy            strategy.schema.json          Strategy.ts         strategy.py          strategies

  Goal                goal.schema.json              Goal.ts             goal.py              goals

  KPI                 kpi.schema.json               KPI.ts              kpi.py               kpis

  Decision            decision.schema.json          Decision.ts         decision.py          decisions

  Bottleneck          bottleneck.schema.json        Bottleneck.ts       bottleneck.py        bottlenecks

  Opportunity         opportunity.schema.json       Opportunity.ts      opportunity.py       opportunities

  Experiment          experiment.schema.json        Experiment.ts       experiment.py        experiments

  Lesson              lesson.schema.json            Lesson.ts           lesson.py            lessons

  Executive Brief     executive-brief.schema.json   ExecutiveBrief.ts   executive_brief.py   executive_briefs
  -------------------------------------------------------------------------------------------------------------

------------------------------------------------------------------------

## Generation Rules

-   JSON Schema is the canonical machine-readable contract.
-   TypeScript interfaces are generated from JSON Schema.
-   Python models are generated from JSON Schema.
-   Database migrations should remain aligned with the canonical schema.

------------------------------------------------------------------------

## Design Principles

-   One source of truth per business object.
-   Prefer generation over manual duplication.
-   Version contracts independently using semantic versioning.
-   Validate data at system boundaries.

------------------------------------------------------------------------

## Used By

-   API Layer
-   SDK Generation
-   Database Migrations
-   Agent Orchestrator
-   Workflow Automation
