# Semantic Pattern — recogniser, generator and test battery

The first working implementation of the **Semantic Pattern** system: brand identities as
generative pattern grammars that machines can identify from any fragment of any surface,
by open measurement rather than trained image recognition. Recognition yields an identity
claim; trust and meaning resolve separately through the brand's own domain.

This repository is the empirical half of a design research project. The source of truth is
the meta-grammar specification in [`spec/`](spec/); the code structure mirrors its sections
so code and spec stay honest against each other.

**Status: research prototype.** Everything here is an experiment in progress, versioned so
experiments can be tracked and compared. Nothing is a final format.

## Principles (from the spec — not negotiable in v1)

1. **Classical, deterministic measurement first.** OpenCV/numpy feature extraction. No
   learned models — the spec allows them later only as accelerants, never as the only path.
2. **Identification only.** No payload encoding, no bounded marks, no fiducial code
   regions. The whole surface is the pattern.
3. **Graceful uncertainty.** Confidence scales with sample size and is reported
   per-feature with the working shown. `candidate: grammar-001, 0.62` is a valid and
   honest output; false certainty is not.
4. **Reproducible everything.** Seeded generation, deterministic pipeline: same inputs →
   same numbers. Published results must be repeatable by anyone.
5. **Relative units only.** All grammar dimensions are module-relative ratios, never
   absolute pixels.

## Repository layout

| Path | What it is |
|---|---|
| `spec/` | The specification and the two pattern grammar audits — the requirements |
| `schemas/grammar-sheet/v0/` | Versioned machine-readable schema for grammar sheets |
| `grammars/` | Grammar sheets (YAML, one per grammar), validated against the schema |
| `generator/` | Grammar sheet + seed + instance params → SVG/PNG surfaces |
| `recogniser/` | Image → normalisation → per-feature measurement → scored identity claims |
| `battery/` | Test harness: synthetic degradations + real photo ingestion |
| `experiments/` | One folder per experiment run, with a manifest (git commit, schema and sheet versions, seeds) and machine-written results — committed, because the results are the research output |
| `tests/` | Unit tests; every feature measurer is tested against synthetic ground truth before pipeline use |
| `SPEC-ISSUES.md` | Places where the spec skeleton was too vague to encode, and the choices made — collected rather than silently decided |

## Getting started

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest
```

## Licence

Code is [MIT](LICENSE). The specification and audit documents are
[CC BY 4.0](LICENSE-docs).
