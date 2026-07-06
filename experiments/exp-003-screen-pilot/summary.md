# exp-003 print-and-photograph -- ingest summary

- generated: 2026-07-06T21:11:55.753980+00:00
- git commit: 27e57e59dc0caae57f8434762bdc4aa6fa39c885
- recogniser: v0 (identical pipeline; no photo-special preprocessing)
- rows: 24  (ok=24)
- recognised: bar-cascade-001 24, iso-002 0

## Per-condition tables

**By lighting**

| lighting | n | mean aggregate | identified | candidate | not_recognised |
|---|---|---|---|---|---|
| normal_room | 24 | 0.020 | 0 | 0 | 24 |

**By angle (deg)**

| angle (deg) | n | mean aggregate | identified | candidate | not_recognised |
|---|---|---|---|---|---|
| 0 | 6 | 0.030 | 0 | 0 | 6 |
| 30 | 12 | 0.021 | 0 | 0 | 12 |
| 60 | 6 | 0.009 | 0 | 0 | 6 |

**By distance**

| distance | n | mean aggregate | identified | candidate | not_recognised |
|---|---|---|---|---|---|
| far_2m | 6 | 0.041 | 0 | 0 | 6 |
| fills_frame | 18 | 0.013 | 0 | 0 | 18 |

**By surface**

| surface | n | mean aggregate | identified | candidate | not_recognised |
|---|---|---|---|---|---|
| 001-s0 | 24 | 0.020 | 0 | 0 | 24 |

## The three Phase-4b questions (exp-002 s6)

**(a) Is real camera white balance diagonal?** insufficient data -- no iso-002 photo recognised with the relationship colour path applicable (need >= 3 inks in frame; SI-020).

**(b) Real clipping on bright inks?** insufficient data -- no iso-002 photo produced a measured ink set to inspect for clipping.

**(c) Does 002's ink set survive the print gamut?** insufficient data -- no iso-002 photo was successfully recognised.

---

Each answer is computed only from photos that recognised; a question with no supporting rows is marked *insufficient data* rather than guessed. L3 (spec s11) stays open until this runs on a real, sufficiently-covered capture set (SI-017, SI-023).
