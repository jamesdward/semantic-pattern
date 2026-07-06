"""The identity claim: the recogniser's public output (spec 8 steps 3-5).

``recognise(image_or_path, grammars_dir)`` is the single public entry point. It
normalises + measures the image once (``recogniser.measure``), scores the result
against every valid sheet in ``grammars_dir`` (``recogniser.score``), and returns
a JSON-serialisable *identity claim*: per-sheet results sorted by confidence,
each with its per-feature working, plus the top-level label the spec demands --

    "identity claim -- unverified until resolution completes"  (spec 8 step 5)

The claim is data, never a verdict of trust: recognition produces a *claim*;
trust is done later by the brand's domain (spec 9). The working (formulas,
normalisation steps, orientation estimate, ambiguities) is part of the output so
two conforming recognisers can be compared (spec 8: "the working is part of the
output"). Everything is deterministic: same image -> byte-identical claim JSON
when dumped with ``sort_keys=True`` (README principle 4).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from sheets import list_sheets
from recogniser import measure as _measure
from recogniser import score as _score

CLAIM_NOTE = "identity claim -- unverified until resolution completes"


def _jsonable(obj):
    """Recursively coerce numpy scalars/arrays to plain Python for json.dumps."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return [_jsonable(v) for v in obj.tolist()]
    if isinstance(obj, float):
        return round(obj, 6)
    return obj


def recognise(image_or_path, grammars_dir="grammars", image_ref=None) -> dict:
    """Recognise ``image_or_path`` against every valid sheet in ``grammars_dir``.

    Returns the identity-claim dict (spec 8). ``image_ref`` labels the image in
    the claim; it defaults to the path string, or ``"<array>"`` for an in-memory
    array so the claim stays reproducible.
    """
    image = _measure.load_image(image_or_path)
    if image_ref is None:
        image_ref = str(image_or_path) if not isinstance(image_or_path, np.ndarray) \
            else "<array>"

    measured = _measure.measure_surface(image)
    sheets = list_sheets(grammars_dir)

    results = [_score.score_sheet(sheet, measured) for sheet in sheets]
    # Sort by confidence, then sheet id, so the order is deterministic on ties.
    results.sort(key=lambda r: (-r["aggregate_confidence"], str(r["sheet_id"])))

    claim = {
        "note": CLAIM_NOTE,
        "image_ref": image_ref,
        "image_shape": list(image.shape),
        "results": results,
        "measurement_working": measured["working"],
        "recogniser_version": "v0",
    }
    return _jsonable(claim)


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Recognise a fragment against grammar sheets; print an identity claim."
    )
    parser.add_argument("image", help="path to a surface/fragment image")
    parser.add_argument("--grammars", default="grammars",
                        help="directory of grammar sheets (default: grammars/)")
    args = parser.parse_args(argv)

    claim = recognise(args.image, grammars_dir=args.grammars)
    print(json.dumps(claim, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
