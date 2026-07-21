---
document_id: DEC-0001
title: Adopt Document Metadata Standard
version: 1.0.0
status: Draft
maturity: D1
owner: AVANZIA
document_type: Architecture Decision Record
authority_level: 3
classification: Internal
created: 2026-07-18
last_updated: 2026-07-18
related:
  - standards/document-metadata.md
  - templates/controlled-document.md
tags:
  - adr
  - governance
  - metadata
---

# DEC-0001 — Adopt Document Metadata Standard

## Status

Draft (D1)

---

## Context

Hermes OS will contain governance documents, standards, specifications, SOPs, and architecture decisions across multiple repositories.

Without a common metadata schema, documents become difficult to identify, search, validate, automate, and govern consistently.

---

## Decision

Hermes OS adopts the **Document Metadata Standard** defined in:

`standards/document-metadata.md`

All new controlled documents shall conform to that standard unless an approved Architecture Decision Record explicitly defines an exception.

---

## Consequences

### Positive

- Consistent document identification
- Better traceability
- Easier automation
- Standardized governance
- Reusable templates

### Trade-offs

- Slightly more effort when creating new documents
- Existing documents may require migration to the standard

---

## Implementation

1. Maintain the metadata standard.
2. Use the controlled document template.
3. Review metadata during document approval.
4. Update documents when the standard evolves.

---

## Review Checklist

- [ ] Technical Review
- [ ] Security Review
- [ ] Owner Approval
- [ ] Git Commit
- [ ] GitHub Push
