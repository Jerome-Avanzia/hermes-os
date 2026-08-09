# SOP: Competitor Intelligence Report

**Skill:** competitor-intelligence  
**SOP ID:** competitor-intelligence/competitor-intelligence  
**Version:** 1.0.0  
**Owner:** Business Strategy  
**Advisory label:** FOUNDER-INTERNAL — ADVISORY ONLY

---

## Purpose

Produce a structured, evidence-grounded competitor intelligence report from publicly
available information retrieved at execution time. Every claim must be traceable to a
retrieved source, labelled as an inference, or explicitly marked as unknown.

---

## Inputs

| Field | Required | Description |
|---|---|---|
| `competitor_name` | Yes | Name of the competitor to research |
| `question` | No | Specific business question or decision the report should address |

---

## Execution Steps

### Step 1 — Domain Resolution

Identify the competitor's primary web domain:
- If `competitor_name` contains a domain (e.g. `Acme (acme.com)`), extract it directly.
- If `competitor_name` is a bare domain, use as-is.
- Otherwise, infer the domain as `{slug}.com` where `slug` is the lowercased, hyphenated name.

### Step 2 — Web Evidence Collection

Fetch the following URLs for the resolved domain. Retain URL and HTTP status for each.

| Priority | URL | Purpose |
|---|---|---|
| P0 | `https://{domain}/` | Homepage — positioning, tagline, primary audience |
| P1 | `https://{domain}/pricing` | Pricing model and tiers |
| P1 | `https://{domain}/about` | Mission, founding story, team size signals |
| P2 | `https://{domain}/blog` | Thought leadership, recent announcements |
| P2 | `https://{domain}/customers` | Target customer segments, case study signals |

Record for each page:
- Whether the fetch succeeded (HTTP 200) or failed (4xx, 5xx, timeout).
- The page title and first 2000 characters of visible text content.

### Step 3 — Evidence Tagging

Apply mandatory evidence tags to every claim in the report:

| Tag | Meaning |
|---|---|
| `[FACT]` | Directly retrieved from a fetched page. Cite the URL. |
| `[INFERRED]` | Logically derived from retrieved facts. State the basis. |
| `[UNKNOWN]` | Information not available from retrieved evidence. Do not guess. |

**Rules:**
- Never invent pricing, headcount, revenue, funding, or customer names.
- If a page fetch fails, record the failure and mark related claims `[UNKNOWN]`.
- Inferences must state their basis explicitly (e.g. "Based on homepage copy targeting
  enterprise buyers...").

### Step 4 — Report Synthesis

Synthesise retrieved evidence into the standard report structure below. The synthesising
profile is the **Business Strategy Consultant** (`strategy-consultant`).

### Step 5 — Deliverable

Return the completed report as structured text. No follow-up LLM call is required after
synthesis.

---

## Report Structure

```
COMPETITOR INTELLIGENCE REPORT
Advisory label: FOUNDER-INTERNAL — ADVISORY ONLY
Generated: {ISO date}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMPETITOR: {name}
QUESTION ADDRESSED: {question or "General competitive landscape"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. COMPANY OVERVIEW
   [Who they are, founding context if known, primary business model]

2. PRODUCT / SERVICE OFFERING
   [What they sell, core capabilities, key differentiators claimed]

3. MARKET POSITIONING
   [How they position — premium/value, niche/broad, emotional/rational]

4. PRICING
   [Model, tiers, price points — or UNKNOWN if not publicly available]

5. TARGET CUSTOMER SEGMENTS
   [Who they serve — firmographic, demographic, use-case signals]

6. RECENT MOVES
   [New products, partnerships, campaigns, hires, funding — dated where available]

7. NEWS & EXTERNAL SIGNALS
   [Press mentions, industry coverage, social signals if retrieved]

8. STRATEGIC IMPLICATIONS
   [What this means for AVANZIA — opportunities, threats, positioning gaps]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EVIDENCE SUMMARY
  Pages fetched: {n}
  Pages successful: {n}
  Pages failed: {list of failed URLs}
  Facts: {count of [FACT] claims}
  Inferences: {count of [INFERRED] claims}
  Unknowns: {count of [UNKNOWN] claims}

SOURCE LOG
  {URL} — {HTTP status} — {title}
  ...
```

---

## Quality Controls

- **No invented facts.** Every non-[UNKNOWN] claim traces to a fetched URL.
- **URLs retained.** All fetched URLs appear in the SOURCE LOG.
- **Inference labelled.** Every deduction is tagged [INFERRED] and its basis stated.
- **Unknowns left unknown.** Do not fill evidence gaps with model memory.
- **Advisory label required.** Every report carries `FOUNDER-INTERNAL — ADVISORY ONLY`.

---

## Failure Handling

| Condition | Behaviour |
|---|---|
| All fetches fail (network error, 403/404) | Produce report with all sections [UNKNOWN]; note evidence collection failed |
| Partial fetch failure | Include successful pages; mark failed sections [UNKNOWN] |
| Competitor name ambiguous | Request clarification before fetching |
| No `question` provided | Scope report as general competitive landscape |
