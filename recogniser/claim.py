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

import cv2
import numpy as np

# Largest frame (px, longest side) the LOCALISED (short-circuit) path measures at.
# A bare fragment normally IS a synthetic surface (<= 1000 px in every test/battery
# config), so this never triggers there and synthetic claims stay byte-identical.
# But a real photo whose pattern already fills >=90% of the frame (e.g. a 60-degree
# fills-frame screen shot) also short-circuits, and running the grid measurer on a
# raw 24-megapixel frame is pathological -- ``extract_flat_inks`` clusters millions
# of unique JPEG colours in Python (minutes). Area-downscaling such a frame to this
# bound before measurement keeps the localised path usable on real captures without
# touching any synthetic input. Set clear of the largest synthetic surface (1000 px)
# so the cap is a no-op for every enrolled test.
LOCALISED_MEASURE_MAX_DIM = 1600

from sheets import list_sheets
from recogniser import measure as _measure
from recogniser import measure_grid as _measure_grid
from recogniser import score as _score
from recogniser import locate as _locate

CLAIM_NOTE = "identity claim -- unverified until resolution completes"

# Measurement dispatch registry, keyed by a sheet's ``structure.type`` (spec 8
# step 2). Each family turns the image into ``{"measurements", "working"}`` in the
# same shape; ``recognise`` runs each family present ONCE on the image (not once
# per sheet) and scores each sheet against its own family's measurements. Adding a
# structure type is one entry here plus a measurer module -- 001 (band) behaviour
# is untouched because it keeps scoring against ``measure.measure_surface``.
STRUCTURE_MEASURERS = {
    "band": _measure.measure_surface,
    "grid": _measure_grid.measure_grid_surface,
}


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


def _measure_families(image, sheets) -> dict:
    """Run each present measurer family ONCE on ``image`` (spec 8 step 2).

    Returns ``{structure_type: {"measurements", "working"}}``. A sheet whose
    structure.type has no measurer family is simply absent here; the caller scores
    it against empty measurements (graceful, never a crash).
    """
    measured_by_type = {}
    for sheet in sheets:
        stype = sheet.get("structure", {}).get("type")
        if stype in measured_by_type or stype not in STRUCTURE_MEASURERS:
            continue
        measured_by_type[stype] = STRUCTURE_MEASURERS[stype](image)
    return measured_by_type


def _recognise_localised(image, image_ref, sheets) -> dict:
    """The pre-SI-025 path: the image IS the surface (bare fragment).

    Byte-identical to recognition before scene localisation existed -- this is the
    branch a synthetic battery fragment (or any ~>=90%-coverage input) takes, which
    is why the ~90% short-circuit in ``locate`` guarantees synthetic claims are
    unchanged. NO localisation/scene keys are added on this path.
    """
    empty = {"measurements": {}, "working": {}}
    # Cap only pathologically large real frames (see LOCALISED_MEASURE_MAX_DIM);
    # a no-op for every synthetic surface, so those claims stay byte-identical.
    longest = max(image.shape[:2])
    if longest > LOCALISED_MEASURE_MAX_DIM:
        s = LOCALISED_MEASURE_MAX_DIM / float(longest)
        measure_image = cv2.resize(image, (max(1, int(round(image.shape[1] * s))),
                                           max(1, int(round(image.shape[0] * s)))),
                                   interpolation=cv2.INTER_AREA)
    else:
        measure_image = image
    measured_by_type = _measure_families(measure_image, sheets)
    results = []
    for sheet in sheets:
        stype = sheet.get("structure", {}).get("type")
        measured = measured_by_type.get(stype, empty)
        results.append(_score.score_sheet(sheet, measured))
    results.sort(key=lambda r: (-r["aggregate_confidence"], str(r["sheet_id"])))

    claim = {
        "note": CLAIM_NOTE,
        "image_ref": image_ref,
        "image_shape": list(image.shape),
        "results": results,
        "measurement_working": {stype: m["working"]
                                for stype, m in measured_by_type.items()},
        "recogniser_version": "v0",
    }
    return _jsonable(claim)


def _recognise_scene(image, image_ref, sheets, regions) -> dict:
    """Scene path (SI-025): locate -> rectify -> measure per candidate region.

    For every candidate region the surface is rectified (perspective-corrected)
    and area-downscaled (anti-capture-moire), then each present family is measured
    on it. Each sheet is scored against its family's measurement on EVERY region
    and keeps its BEST region (highest aggregate). The claim records, per region,
    the bbox and rectification working, and per sheet which region won -- the
    working is part of the output (spec 8).
    """
    empty = {"measurements": {}, "working": {}}
    per_region = []   # one entry per candidate region: {bbox, locate, rectify, measured_by_type}
    for region in regions:
        warped, rect_working = _locate.rectify(image, region, sheets)
        measure_input = _locate.measurement_resample(warped)
        measured_by_type = _measure_families(measure_input, sheets)
        per_region.append({
            "bbox": list(region["bbox"]),
            "locate": region.get("working", {}),
            "rectify": rect_working,
            "measure_input_shape": list(measure_input.shape),
            "measured_by_type": measured_by_type,
        })

    results = []
    for sheet in sheets:
        stype = sheet.get("structure", {}).get("type")
        best = None            # (score_result, region_index)
        for ridx, reg in enumerate(per_region):
            measured = reg["measured_by_type"].get(stype, empty)
            scored = _score.score_sheet(sheet, measured)
            if best is None or scored["aggregate_confidence"] > best[0]["aggregate_confidence"]:
                best = (scored, ridx)
        scored, ridx = best
        # Annotate the winning result with the region it came from (spec 8 working).
        won = per_region[ridx]
        scored = dict(scored)
        scored["region"] = {"index": ridx, "bbox": won["bbox"],
                            "rectified": won["rectify"].get("rectified", False)}
        results.append(scored)
    results.sort(key=lambda r: (-r["aggregate_confidence"], str(r["sheet_id"])))

    localisation = {
        "stage": "locate -> rectify -> measure per region (SI-025)",
        "n_regions": len(per_region),
        "regions": [{"index": i, "bbox": r["bbox"], "locate": r["locate"],
                     "rectify": r["rectify"],
                     "measure_input_shape": r["measure_input_shape"]}
                    for i, r in enumerate(per_region)],
    }
    # Measurement working: the winning region's family working (the reading that
    # produced each sheet's result). Keyed by structure type as before, so readers
    # and the ingest see the same shape.
    best_region_by_type = {}
    for r in results:
        stype = None
        for sheet in sheets:
            if sheet.get("sheet", {}).get("id") == r["sheet_id"]:
                stype = sheet.get("structure", {}).get("type")
                break
        if stype and stype not in best_region_by_type:
            ridx = r.get("region", {}).get("index", 0)
            m = per_region[ridx]["measured_by_type"].get(stype)
            if m is not None:
                best_region_by_type[stype] = m["working"]

    claim = {
        "note": CLAIM_NOTE,
        "image_ref": image_ref,
        "image_shape": list(image.shape),
        "results": results,
        "measurement_working": best_region_by_type,
        "localisation": localisation,
        "recogniser_version": "v0",
    }
    return _jsonable(claim)


def recognise(image_or_path, grammars_dir="grammars", image_ref=None) -> dict:
    """Recognise ``image_or_path`` against every valid sheet in ``grammars_dir``.

    Returns the identity-claim dict (spec 8). ``image_ref`` labels the image in
    the claim; it defaults to the path string, or ``"<array>"`` for an in-memory
    array so the claim stays reproducible.

    Scene handling (SI-025): the image is first passed to ``locate`` to find
    candidate surface regions. A bare fragment (~>=90% ink-compatible coverage)
    short-circuits to the byte-identical pre-SI-025 path; a scene (a photograph
    that is mostly not-pattern) takes the locate -> rectify -> per-region path.
    """
    image = _measure.load_image(image_or_path)
    if image_ref is None:
        image_ref = str(image_or_path) if not isinstance(image_or_path, np.ndarray) \
            else "<array>"

    sheets = list_sheets(grammars_dir)

    regions = _locate.find_candidate_regions(image, sheets)
    if len(regions) == 1 and regions[0].get("working", {}).get("already_localised"):
        return _recognise_localised(image, image_ref, sheets)
    return _recognise_scene(image, image_ref, sheets, regions)


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
