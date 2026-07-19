# API Development Guide

**Document:** API Development Guide\
**Status:** Draft v1.0

## Purpose

This guide defines how Hermes OS APIs should be designed and implemented
using the canonical contracts in the `contracts/` directory.

------------------------------------------------------------------------

## API Design Principles

-   OpenAPI is the public API contract.
-   JSON Schema is the canonical data contract.
-   Every endpoint must validate requests and responses.
-   APIs should be versioned (`/v1`, `/v2`, ...).
-   Breaking changes require a new API version.

------------------------------------------------------------------------

## Resource Model

  Resource          Endpoint
  ----------------- ---------------------
  Business          `/businesses`
  Strategy          `/strategies`
  Goal              `/goals`
  KPI               `/kpis`
  Decision          `/decisions`
  Bottleneck        `/bottlenecks`
  Opportunity       `/opportunities`
  Experiment        `/experiments`
  Lesson            `/lessons`
  Executive Brief   `/executive-briefs`

------------------------------------------------------------------------

## Standard Operations

Each resource should support:

-   Create
-   Retrieve
-   Update
-   Delete
-   List

------------------------------------------------------------------------

## Validation

-   Validate all payloads against JSON Schemas.
-   Reject invalid requests with HTTP 400.
-   Return structured error objects.

------------------------------------------------------------------------

## Used By

-   REST API
-   MCP Tools
-   SDK Generation
-   Workflow Automation
