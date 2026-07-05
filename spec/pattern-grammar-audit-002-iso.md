# Pattern Grammar Audit — Worked Example 002

**Subject:** ISO identity by Studio.Build ("Design intervention for a complex world™") — four clean digital variants + application shots
**Method:** pixel-level measurement across variants — edge-projection autocorrelation for grid detection, run-length analysis for primitives, colour arithmetic for blend verification
**Status:** family audit · genome recovered · dialects mapped

**Purpose of this audit:** methodology validation on a public identity. Nothing here is enrolment — that is the brand owner's act. What this demonstrates is that a multi-variant generative identity yields a family grammar under measurement

---

## 1. The question this audit tests

Example 001 was one static pattern. Real identities are families — many compositions, palettes and applications that "look wildly different" while being unmistakably one thing. Can measurement recover what is invariant (the genome) versus what varies (the dialects)?

Answer: yes, decisively

---

## 2. The invariant genome (found in every variant)

### The grid

Edge-projection autocorrelation finds one dominant square lattice per variant: **72 px** in variant 1, **69 px** in variants 5–7 (same design at slightly different render scale). Peaks recur at exact integer multiples (×2, ×4, ×6, ×8) — a strict cell system with structure at multiple cell scales

### The primitive alphabet (all dimensions in modules, M)

| Primitive | Measured | In modules |
|---|---|---|
| Filled cell | 69–72 px squares | 1×1 M |
| Inscribed circle | run-width peaks 66–70 px and 129–137 px | Ø 1 M and Ø 2 M |
| Stripe bar | height 33–34 px, gap 35–36 px | height M/2, pitch 1 M, duty 1/2 |
| Staircase diagonal | steps measured (72,72), (72,70), (73,70), (72,71) px | 1 M × 1 M — exact 45° cell steps |
| Rounded caps / quarter-rounds | radii consistent with M/2 | stadium bars, quarter-circle cells |

Five primitives. Everything in every variant is built from them, on the grid, at 1 M or 2 M scale, with the stripe sub-rhythm at exactly half-module

### The overprint rule — and it is exact

Overlap colours were tested against the multiply product of their parent inks (channel-wise, c₁·c₂/255):

| Overlap | Predicted (multiply) | Measured | Verdict |
|---|---|---|---|
| green × yellow | (115, 251, 33) | (115, 251, 33) | **exact** |
| orange × teal-grey | (139, 76, 32) | (139, 76, 32) | **exact** |
| green × teal-grey | (69, 200, 76) | (69, 200, 76) | **exact** |
| teal × yellow | (70, 159, 46) | (72, 159, 58) | within AA tolerance |
| teal × light-blue | (41, 123, 137) | (54, 124, 138) | near |

This is print-separation logic in digital form: flat spot inks, multiply overprint. **Every place two inks cross, the overlap colour is *derivable* from its parents.** The identity carries its own internal arithmetic

### The ground

White. Consistently — the ink system needs it, exactly as overprint needs paper

---

## 3. The dialects (what varies per variant)

| Variant | Ink subset (dominant) | Composition |
|---|---|---|
| 1 / 5 (banner) | yellow FAFF54, teal 479F8C, magenta D32EB7, light-blue 95C3F9, red D02C3A | three glyph-like clusters |
| 6 (banner) | blue 3B87F7, green 75FB63, magenta D52EB2, red C5111D, yellow FAFF54, orange EC5F2A | clusters + smiley figure |
| 7 (banner) | magenta D52EB2, orange EC5F2A, green 75FB63, red C5111D, teal-grey 96CBC4 | clusters + circle chains |
| Poster / phone crops | subsets of the above | dense fields, single figures |

Key finding: **the inks recur across variants** — magenta D52EB2, red C5111D, green 75FB63, yellow FAFF54 each appear in multiple variants. There is a master ink set; each variant speaks with a subset. (Variant 1's magenta measures D32EB7 vs D52EB2 elsewhere — within rendering tolerance of the same ink)

The compositions are figurative and free — glyphs, creatures, smileys. **The genome is the construction system, not the picture.** This is exactly the family structure the meta-grammar predicts: fixed alphabet and rules, free expression within them

---

## 4. The recovered family grammar

```yaml
grammar:
  name: iso/studio-build (audit reconstruction — not an enrolment)
  ground: white
  grid:
    module: M (square)
    scales: [1M, 2M]
  primitives:
    - cell 1x1
    - circle Ø1M, Ø2M (inscribed)
    - stripe: height M/2, pitch 1M, duty 0.5, horizontal
    - staircase: 1M x 1M steps (45° in cells)
    - rounded caps: radius M/2
  colour:
    model: flat spot inks on white, multiply overprint
    master_inks: [FAFF54, 479F8C, D52EB2, 3B87F7, 75FB63,
                  C5111D, EC5F2A, 95C3F9, 96CBC4]
    overlap_rule: multiply (verified exact)
  variation:
    per_variant: ink subset, composition, density
    invariant: grid, alphabet, stripe rhythm, overprint rule, ground
```

---

## 5. Fragment strength — dramatically better than Example 001

Example 001's signature lived in band transitions; single-band fragments were weak. This grammar distributes its genome almost everywhere:

| Any fragment containing… | Recovers |
|---|---|
| A few cells of anything | module M, grid alignment |
| One circle | Ø/M ratio (1 or 2), primitive vocabulary |
| A striped area | the M/2 · duty-1/2 rhythm |
| Any two-ink overlap | the multiply arithmetic — a *self-verifying* colour check |
| A diagonal edge | the 1 M staircase |
| Almost anywhere | ink values against the master set |

A note on the overlap rule, honestly weighted: multiply is a standard blend mode — a tool default, not an invention — so "uses multiply" identifies nothing. Its value is narrower: because the rendering rule is committed and deterministic, a fragment where inks cross contains parents and product together, and c₁ × c₂ ≠ c₃ flags an unfaithful reproduction. Verification, not identification. The identifying content is the specific ink values, never the rule that combines them

Remaining weaknesses: 90° rotation ambiguity from grid alone (broken by stripe orientation, fully broken by composition); and the figurative layer contributes recognition for humans but nothing measurable — correctly so

---

## 6. Distinctiveness assessment — the honest part

The structural values sit on canonical peaks: square grid, duty 1/2, 45° steps, multiply blend. Pixel-grid design is a crowded genre. Structure alone would false-positive against half of it

And a second peak hides in the colour dimension: **multiply blend is a tool default** — the most canonical value in its space. Which generalises to a methodology rule this audit initially got wrong:

**Chosen versus defaulted.** Measurement cannot distinguish a loving design decision from a software default — square grids may be artboard settings, duty 1/2 a default, multiply a dropdown. It doesn't need to distinguish them, provided peak-discounting is applied without exception: defaults are canonical peaks by definition, and canonical peaks carry no signature weight however precisely they measure. Exact ≠ distinctive

After discounting, what actually carries the identification load:

1. **The exact ink set.** Nine specific RGB values are coordinates in colour space the way 1.94 was in ratio space. FAFF54 ∧ D52EB2 ∧ 75FB63 ∧ C5111D co-occurring is already rare; the full set is an address
2. **The primitive mix and composition statistics** — the proportions in which the alphabet is used, which are choices no tool defaults
3. **The structural conjunction** (grid ∧ alphabet ∧ M/2 rhythm) contributes weakly — each element near-canonical — with the multiply rule contributing verification only, not identification

Were this a real enrolment, the tune conversation would start with the structural peaks (duty 1/2 → 0.47? staircase 1:1 → 1:0.94?) — but note the genuine trade-off: this identity's charm partly *is* its canonical pixel-grid honesty. Tuning here would be a design negotiation, not a formality. The likely resolution: let the ink set and overlap arithmetic carry identification, leave the structure canonical, and accept a stated dependence on colour fidelity

Which surfaces the real cost: **colour-led signatures are print-fragile.** The audit ran on clean digital renders; the poster photo in this set already shows shifted values. A print enrolment would commit ink *relationships* (orderings, ratios, multiply consistency) rather than absolute values — measurable under lighting variation where absolutes are not

---

## 7. What this audit proves for the thesis

1. **Family grammars are recoverable by measurement.** Four visually different variants yielded one genome: five primitives, one grid, one rhythm, one colour rule
2. **"Brands rarely use just one pattern" is not a problem — it is the structure the meta-grammar expects.** Invariants = genome; variants = dialects; the audit separates them empirically
3. **Generative identities are already speaking grammar.** Studio Build committed to a module, an alphabet, a duty cycle and a blend rule — whether or not they ever wrote those numbers down. The audit just wrote them down
4. **Distinctiveness lives in different places for different grammars.** 001's lived in ratios; ISO's lives in inks and their arithmetic. The meta-grammar must let each identity declare *where* its signature lives

---

## 8. One sentence

**Four compositions that look nothing alike measured as one language — five primitives on one grid with one rhythm and one committed ink set — and the honest lesson that precision is not distinctiveness: the grammar's structure sits on tool-default peaks, so its identity lives in its inks and its proportions, not its rules**
