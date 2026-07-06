# exp-003 print-and-photograph -- ingest summary

- generated: 2026-07-06T22:48:11.134899+00:00
- git commit: 5ad8a08fa39b5a856be8d5fd2791fd27ef0eb4e0
- recogniser: v0 (identical pipeline; no photo-special preprocessing)
- rows: 24  (ok=24)
- recognised: bar-cascade-001 24, iso-002 0

## Per-condition tables

**By lighting**

| lighting | n | mean aggregate | identified | candidate | not_recognised |
|---|---|---|---|---|---|
| normal_room | 24 | 0.201 | 0 | 4 | 20 |

**By angle (deg)**

| angle (deg) | n | mean aggregate | identified | candidate | not_recognised |
|---|---|---|---|---|---|
| 0 | 6 | 0.276 | 0 | 2 | 4 |
| 30 | 12 | 0.195 | 0 | 2 | 10 |
| 60 | 6 | 0.140 | 0 | 0 | 6 |

**By distance**

| distance | n | mean aggregate | identified | candidate | not_recognised |
|---|---|---|---|---|---|
| far_2m | 6 | 0.123 | 0 | 0 | 6 |
| fills_frame | 18 | 0.227 | 0 | 4 | 14 |

**By surface**

| surface | n | mean aggregate | identified | candidate | not_recognised |
|---|---|---|---|---|---|
| 001-s0 | 24 | 0.201 | 0 | 4 | 20 |

## The three Phase-4b questions (exp-002 s6)

**(a) Is real camera white balance diagonal?** insufficient data -- no iso-002 photo recognised with the relationship colour path applicable (need >= 3 inks in frame; SI-020).

**(b) Real clipping on bright inks?** insufficient data -- no iso-002 photo produced a measured ink set to inspect for clipping.

**(c) Does 002's ink set survive the print gamut?** insufficient data -- no iso-002 photo was successfully recognised.

---

Each answer is computed only from photos that recognised; a question with no supporting rows is marked *insufficient data* rather than guessed. L3 (spec s11) stays open until this runs on a real, sufficiently-covered capture set (SI-017, SI-023).
