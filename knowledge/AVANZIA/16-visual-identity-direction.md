# AVANZIA Visual Identity Direction

## Design Principle

Every visual decision serves the brand. If a color, typeface, or layout
choice cannot be traced back to a brand attribute, it does not belong.

AVANZIA's visual identity communicates the same things its words do:
precision, structure, trust, intelligence, and long-term thinking. The
design is not a wrapper for the brand — it is an expression of it.

The primary design mode is dark. The light mode palette is defined for
contexts where dark mode is not appropriate (print, documents, client
materials) but the dark experience is the canonical AVANZIA expression.

---

## Color Palette

### Rationale

Colors were derived from three brand attributes in order of priority:

1. **Integrity + Build for the Long Term** → colors must carry weight
   and permanence; nothing light, trendy, or disposable
2. **Excellence Through Simplicity** → a restricted palette; every
   color must justify its presence
3. **Human Judgment, AI Leverage** → not cold or clinical; controlled
   warmth against the depth of the dark backgrounds

The AI industry defaults to neon blues, gradient orbs, and purple-to-
blue gradients. AVANZIA does not. Trend-chasing contradicts the core
value of building for the long term.

---

### Dark Mode (Primary)

**Background — Abyss**
`#080E1A`
The deepest surface. Used for page backgrounds. A very dark navy with a
controlled blue undertone — creates depth without pure black's flatness.
Not black: pure black feels absolute and closed. This color feels like
late-night precision work, which is exactly the register AVANZIA
operates in.

**Surface — Midnight**
`#0F1829`
Primary card and section surfaces. Elevated above the background without
competing with it. The slight lift creates structure — consistent with
Systems Over Heroics: layers that are visible and purposeful.

**Elevated Surface — Structure**
`#1A2640`
Secondary cards, hover states, borders. The architectural layer — gives
form and definition to layout elements without relying on heavy lines.

**Primary Text — Clarity**
`#F0F4FF`
Near-white with a controlled blue cast that harmonizes with the dark
navy backgrounds. Not pure white (#FFFFFF), which creates excessive
contrast and feels harsh. This white has composure.

**Secondary Text — Depth**
`#8A9BBF`
Supporting copy, labels, metadata. Readable but clearly subordinate to
primary text. Maintains accessibility contrast on all surface colors.

**Accent — Signal**
`#2460E0`
The single active color. Used for primary CTAs, links, active states,
and key visual emphasis. A deliberate, slightly deeper blue than
standard SaaS blue — more composed, less urgent. Chosen because:
- It signals intelligence and precision (consistent with the AI-leverage
  positioning) without the hype of neon
- It is saturated enough to command attention when needed
- It reads as confident, not aggressive

**Accent Hover — Signal Active**
`#1A4FC0`
The deeper state of the accent, used on interactive hover and press.
Maintains the same register; avoids cheap brightness-increase hover
effects.

---

### Light Mode (Secondary)

**Background — Paper**
`#F8FAFC`
Near-white with the faintest cool cast. Not pure white — this has
material quality, like premium paper.

**Surface — Lifted**
`#FFFFFF`
Card and section surfaces lift cleanly against the paper background.

**Elevated Surface — Structure**
`#E2E8F0`
Borders, dividers, secondary cards.

**Primary Text — Ink**
`#0B1120`
The same deep navy from the dark mode background, now used as text.
Visual continuity between modes: the darkest color in the system always
carries meaning.

**Secondary Text — Slate**
`#475569`
Supporting copy, metadata, labels.

**Accent — Signal**
`#2460E0`
Identical accent across both modes — the brand is consistent regardless
of context.

---

### Color Rules

- The accent color appears sparingly. One primary CTA per view is
  sufficient. Overuse destroys its signal value.
- Do not introduce additional colors without explicit justification
  against a brand attribute. The restricted palette is a feature, not
  a limitation.
- No gradients as primary design elements. A subtle gradient may appear
  in illustrations or the hero background, but only where it adds depth
  to a structural composition — never as decoration.
- No colored backgrounds for section differentiation. Use surface
  elevation and spacing instead.

---

## Typography

### Rationale

Typography is the primary design element for a company whose product is
systems, strategy, and intelligence. The typeface choices reflect:

- **Clear over clever** (tone of voice principle) → no quirky or
  expressive display fonts; clean, legible, purposeful
- **Confident without arrogance** → not heavy or aggressive; measured
  weight, confident at large sizes
- **Timeless, not trendy** → humanist or geometric sans-serifs with
  strong technical heritage; not novelty fonts

---

### Heading Font: Plus Jakarta Sans

A humanist geometric sans-serif. Used for all headings, hero text,
navigation, and prominent labels.

**Why Plus Jakarta Sans:**
- Humanist construction gives it warmth, avoiding the clinical coldness
  of pure geometric fonts — consistent with "Human Judgment, AI
  Leverage" (not robotic, not cold)
- Geometric proportions convey precision and structure — consistent with
  "Systems Over Heroics" and "Excellence Through Simplicity"
- Excellent performance at large display sizes: the character of the
  letters holds at 80px+ without becoming decorative
- Less ubiquitous than Inter at heading sizes, giving AVANZIA a
  distinctive but professional voice
- Works in all weights from Light (300) to ExtraBold (800); primary
  use is SemiBold (600) and Bold (700)

Source: Google Fonts (open license, web-safe, performant)

### Body Font: Inter

Used for all body copy, descriptions, form elements, and UI text.

**Why Inter:**
- Designed specifically for screen readability — optimal at 14–18px
  where most body text lives
- Neutral enough not to compete with Plus Jakarta Sans in headings
- Technical precision without coldness — right register for a company
  that values clarity and execution
- The standard for professional web applications; feels established
  and trustworthy, not experimental

Source: Google Fonts (open license, web-safe, performant)

### Monospace Font: JetBrains Mono

Used for code snippets, technical references, terminal output, and
any context where AVANZIA's technical precision must be made visible.

**Why JetBrains Mono:**
- Designed for developers and technical reading: high legibility at
  small sizes
- Its presence signals technical rigor — when AVANZIA shows code or
  system output, it should look intentional
- Consistent with Hermes-related content where system interactions are
  displayed

Source: Google Fonts (open license)

---

### Typography Scale

All sizes are base values; final implementation uses a responsive scale.

| Role | Font | Weight | Size (desktop) | Tracking |
|------|------|--------|----------------|---------|
| Hero headline | Plus Jakarta Sans | Bold 700 | 64–80px | -0.02em |
| H1 | Plus Jakarta Sans | Bold 700 | 48px | -0.02em |
| H2 | Plus Jakarta Sans | SemiBold 600 | 36px | -0.01em |
| H3 | Plus Jakarta Sans | SemiBold 600 | 24px | 0 |
| H4 / Label | Plus Jakarta Sans | Medium 500 | 14px | 0.08em (uppercase) |
| Body large | Inter | Regular 400 | 18px | 0 |
| Body | Inter | Regular 400 | 16px | 0 |
| Caption | Inter | Regular 400 | 14px | 0 |
| Code | JetBrains Mono | Regular 400 | 14px | 0 |

**Line height:** 1.6 for body text, 1.2 for headings.

**Typography rules:**
- Headlines at H1 and above should rarely exceed two lines on desktop.
  If a headline needs three lines, it is trying to say too much.
- All-caps is reserved for section labels and navigation elements only.
  Body copy is never all-caps.
- Do not mix weights within a single line of running text.

---

## Logo Concept Brief

### What the logo must communicate

In one visual impression, the logo must convey:
- Precision and intelligence (not cleverness, not complexity)
- Structure — the sense that this company builds systems
- Trust — something that would appear on a serious business document
  without embarrassment
- Forward direction without motion clichés (no swooshes, no arrows)

### What the logo must not be

- A neural network node diagram, circuit board pattern, or brain
  silhouette — these are AI category clichés that every competitor uses
- A gradient orb or abstract 3D shape — signals hype, not substance
- An illustrative or pictorial mark — too specific, limits scalability
  across contexts
- Excessively bold or heavy weight — AVANZIA is confident, not loud

### Mark direction

**Primary recommendation: Geometric wordmark with a minimal companion
mark**

The wordmark sets AVANZIA in Plus Jakarta Sans, SemiBold or Bold, with
deliberate letterspacing. The name is strong enough to stand alone in
most contexts.

The companion mark should be an abstract geometric form — not a letter,
not a representational icon. Candidate direction:

A structured grid or matrix motif suggesting systems thinking: four or
nine equal squares arranged in a 2×2 or 3×3 grid, with one unit
highlighted or offset — communicating "a system with a point of
emphasis." This is structural without being illustrative. It reduces
cleanly to a favicon, embosses on materials, and works in single color.

Alternative direction: The letter A as a pure geometric construction —
an equilateral triangle with a horizontal crossbar, set at a precise
proportion. Architectural, forward-pointing. Simple enough to hold at
16px.

### Logo lockup variants required

- **Horizontal:** mark + wordmark (primary use)
- **Stacked:** mark above wordmark (secondary, square contexts)
- **Mark only:** for favicon, app icon, embossing
- **Wordmark only:** for contexts where mark clutters (email signatures,
  document headers)

### Color variants required

- Light (white mark + wordmark) on dark backgrounds — primary
- Dark (Abyss mark + wordmark) on light backgrounds — secondary
- Accent (Signal blue mark + wordmark) on dark backgrounds — special
  use only; not for general deployment

### Technical requirements

- Delivered in SVG (primary), PNG at 2× and 4× resolutions
- All variants must pass WCAG AA contrast on their intended backgrounds
- Mark must be legible at 16×16px favicon size

---

## Mood and Visual Experience

### Five defining adjectives

**Composed.**
Nothing is competing for attention. Every element has earned its place.
Visual hierarchy is clear: the most important thing is obviously the
most important thing. Whitespace is not emptiness — it is decision.

**Structural.**
The layout feels designed rather than assembled. Grid systems are
visible in the discipline of alignment. Components relate to each other
with purpose. The architecture of the page reflects the architecture of
the company's thinking.

**Precise.**
Details are exact. 8px spacing increments. Consistent border radius
across components. Aligned baselines. The precision is not performative
— it is a byproduct of caring about quality in every dimension. This is
what "Excellence Through Simplicity" looks like on screen.

**Substantive.**
Dark, weighted, considered. The visual impression is one of depth and
seriousness — not heaviness, not oppressiveness, but the quality of a
company that has thought carefully about what it does and is not
performing enthusiasm. A first-time visitor should feel: this company
knows what it is talking about.

**Trusted.**
The identity does not rely on visual tricks to create impressions it
cannot back up. No fake social proof imagery. No stock photos of
diverse teams in glass offices. No animated statistics counting up to
impressive numbers. The design is honest — as honest as the brand it
represents.

---

### What AVANZIA looks like: described references

These are described aesthetic references — not links. A designer should
use them as a register, not a template.

**Linear (the product):** The visual language of a precision tool.
Tight typography, deliberate spacing, dark mode that feels engineered
rather than styled. AVANZIA shares this sensibility around structure and
density of information — without copying the product aesthetic.

**A well-designed annual report from an architecture firm:** Premium
paper quality, considered whitespace, typography that is chosen rather
than defaulted. The sense that someone made decisions, rather than
accepted defaults. AVANZIA's materials — website, decks, documents —
should have this quality.

**A technical reference book from a respected publisher:** No decoration
for decoration's sake. Clear hierarchy. Content that trusts the reader
to engage with it without being entertained first. This is the quality
AVANZIA aims for in text-heavy contexts.

---

### What AVANZIA does not look like

- Gradient-heavy AI startup pages with glowing orbs and abstract
  particle animations (signals hype, not substance)
- Soft, rounded, pastel SaaS products (signals approachability at the
  expense of credibility)
- Aggressive growth-hacking design with high-contrast orange CTAs and
  artificial urgency copy (contradicts Integrity Before Growth)
- Generic corporate consulting websites with stock photography and
  blue-white-gray color schemes (indistinguishable and unambitious)
- Maximalist design with competing visual elements (contradicts
  Excellence Through Simplicity)
