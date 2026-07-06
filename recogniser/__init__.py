"""Recogniser: fragment -> identity claim (spec section 8).

The recogniser is split so measurement can be unit-tested without scoring:

  * ``recogniser.measure`` -- classical, deterministic image measurers
    (image -> locus features, with honest sample counts).
  * ``recogniser.score``  -- tolerance-normalised, sample-size-scaled scoring of
    measured features against a grammar sheet (SI-001/SI-002).
  * ``recogniser.claim``  -- the public ``recognise`` entry point and the
    JSON-serialisable identity claim (spec 8 steps 3-5).

CLI: ``python -m recogniser <image> [--grammars grammars/]``.
"""

from recogniser.claim import recognise

__all__ = ["recognise"]
