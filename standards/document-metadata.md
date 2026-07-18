---
document_id: STD-0001
title: Document Metadata Standard
version: 1.0.0
status: Draft
maturity: D1
owner: AVANZIA
classification: Internal
document_type: Standard
authority_level: 2
---

# Document Metadata Standard

## Purpose

This standard defines the metadata required for every controlled document in the Hermes OS ecosystem.

The objectives are to:

- Provide consistent identification.
- Support automation.
- Improve traceability.
- Enable governance across repositories.

## Required Fields

| Field | Description |
|------|-------------|
| document_id | Unique document identifier |
| title | Human-readable title |
| version | Semantic version |
| status | Lifecycle status (Draft, Active, Superseded, Archived) |
| maturity | D0–D5 maturity level |
| owner | Organization responsible |
| document_type | Operating Model, Standard, ADR, Specification, SOP, etc. |
| authority_level | Governance hierarchy (1–6) |
| classification | Public, Internal, Confidential |
| created | Creation date |
| last_updated | Last modification date |

## Optional Fields

- effective_date
- approved_by
- security_review
- security_reviewer
- review_cycle
- related
- tags
- slug

## Authority Levels

| Level | Document |
|------:|----------|
| 1 | Operating Model |
| 2 | Standard / Policy |
| 3 | Architecture Decision Record |
| 4 | Specification |
| 5 | Standard Operating Procedure |
| 6 | Implementation Documentation |

## Maturity Levels

| Code | Meaning |
|------|---------|
| D0 | Placeholder |
| D1 | Initial Draft |
| D2 | Reviewed |
| D3 | Approved |
| D4 | Baseline |
| D5 | Superseded |

## Classification Levels

- Public
- Internal
- Confidential

## Design Principles

1. Metadata must be machine-readable.
2. Required fields are mandatory.
3. Optional fields should only be used when meaningful.
4. Every controlled document follows this standard.

