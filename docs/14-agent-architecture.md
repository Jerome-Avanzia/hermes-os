# Agent Architecture

**Document:** Agent Architecture\
**Status:** Draft v1.0

## Purpose

This document defines the major AI agents within Hermes OS, their
responsibilities, and how they collaborate using the shared business
object model.

------------------------------------------------------------------------

## Core Principles

-   Every agent has a single primary responsibility.
-   Agents exchange structured business objects rather than free-form
    text.
-   All recommendations are traceable to source data.
-   The Decision Engine coordinates, but does not replace, specialized
    agents.

------------------------------------------------------------------------

## Core Agents

  ------------------------------------------------------------------------------------
  Agent        Primary Responsibility               Inputs           Outputs
  ------------ ------------------------------------ ---------------- -----------------
  Business     Assess current business state        Business, KPI,   Business
  Analyst                                           Goals            assessment

  Strategy     Evaluate strategic alignment         Strategy, Goals  Strategic
  Advisor                                                            recommendations

  Decision     Prioritize actions                   Opportunities,   Ranked decisions
  Advisor                                           Bottlenecks,     
                                                    Lessons          

  Experiment   Design validation tests              Decisions,       Experiment plans
  Planner                                           Opportunities    

  Executive    Produce management summaries         All business     Executive Brief
  Brief                                             objects          
  Generator                                                          

  Memory       Maintain organizational knowledge    Decisions,       Updated business
  Manager                                           Experiments,     memory
                                                    Lessons          
  ------------------------------------------------------------------------------------

------------------------------------------------------------------------

## Collaboration Flow

``` text
Business Data
      ↓
Business Analyst
      ↓
Strategy Advisor
      ↓
Decision Advisor
      ↓
Experiment Planner
      ↓
Memory Manager
      ↓
Executive Brief Generator
```

------------------------------------------------------------------------

## Shared Data Model

All agents operate on the common Hermes business objects:

-   Business
-   Strategy
-   Goal
-   KPI
-   Decision
-   Bottleneck
-   Opportunity
-   Experiment
-   Lesson
-   Executive Brief

------------------------------------------------------------------------

## Design Principles

-   Stateless execution where possible.
-   Shared structured memory.
-   Explainable recommendations.
-   Modular replacement of individual agents.
-   Versioned prompts and workflows.

------------------------------------------------------------------------

## Used By

-   Decision Engine
-   Workflow Automation
-   Agent Orchestrator
-   Future API Services
