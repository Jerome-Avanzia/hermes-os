# SOP: Lead / Company Research

**Skill:** lead-research  
**SOP ID:** lead-research/lead-research  
**Version:** 1.0.0  
**Owner:** Sales & Business Development  
**Advisory label:** FOUNDER-INTERNAL — ADVISORY ONLY

---

## Purpose

Produce a structured, evidence-grounded lead intelligence report for a named company
from publicly available information retrieved at execution time. Every claim must be
traceable to a retrieved source, labelled as an inference, or explicitly marked as
unknown. The report is internal sales intelligence only — no outreach or external
action is taken.

---

## Inputs

| Field | Required | Description |
|---|---|---|
| `company_name` | Yes | Name of the company to research |
| `question` | No | Specific qualification question or context (e.g. "Are they a fit for AVANZIA?") |

---

## Execution Steps

### Step 1 — Domain Resolution

Identify the company's primary web domain:
- If `company_name` contains a domain (e.g. `Acme (acme.com)`), extract it directly.
- If `company_name` is a bare domain, use as-is.
- Otherwise, infer the domain as `{slug}.com` where `slug` is the lowercased name with non-alphanumeric characters removed.

### Step 2 — Web Evidence Collection

Fetch the following URLs for the resolved domain. Retain URL and HTTP status for each.

| Priority | URL | Purpose |
|---|---|---|
| P0 | `https://{domain}/` | Homepage — positioning, tagline, primary audience |
| P0 | `https://{domain}/about` | Mission, founding story, team size signals |
| P1 | `https://{domain}/pricing` | Pricing model, tiers, business model signals |
| P1 | `https://{domain}/team` | Leadership signals, key decision-makers |
| P2 | `https://{domain}/blog` | Recent activity, company focus, growth signals |

Record for each page:
- Whether the fetch succeeded (HTTP 200) or failed (4xx, 5xx, timeout).
- The page title and first 3,000 characters of visible text.

### Step 3 — Evidence Labelling

Tag every claim in the synthesis with exactly one of:

| Label | Meaning |
|---|---|
| `[FACT]` | Directly retrieved from a fetched page. Cite the URL in parentheses. |
| `[INFERRED]` | Derived from retrieved facts. State the basis explicitly. |
| `[UNKNOWN]` | Not available from evidence. Do not guess. |

Never invent names, financials, employee counts, contracts, or customer relationships.
If a page fetch failed, mark all claims that would depend on it as [UNKNOWN].

### Step 4 — Report Synthesis

Produce the Lead Intelligence Report using the structure below. Every section must be
populated or explicitly marked [UNKNOWN].

---

## Report Structure

```
LEAD INTELLIGENCE REPORT
Advisory label: FOUNDER-INTERNAL — ADVISORY ONLY

COMPANY: {company_name}
QUALIFICATION CONTEXT: {question or "General lead qualification"}

1. COMPANY OVERVIEW
2. PRODUCTS / SERVICES
3. TARGET MARKET
4. BUSINESS MODEL
5. LEADERSHIP & COMPANY SIGNALS
6. LIKELY AVANZIA-RELEVANT OPPORTUNITIES
7. UNKNOWNS / GAPS
8. EVIDENCE SUMMARY
   Pages fetched: N / Pages successful: N / Pages failed: [list]

SOURCE LOG
   (each URL with HTTP status and title)
```

---

## Advisory Constraints

- Public information only. No proprietary, confidential, or fabricated data.
- No external actions: do not send emails, submit forms, or initiate contact.
- Advisory label required on every report: FOUNDER-INTERNAL — ADVISORY ONLY.
- All outreach and qualification decisions belong to the human Sales team.
