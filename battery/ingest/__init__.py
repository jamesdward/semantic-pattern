"""Phase-4b real-photo ingestion path -- STRUCTURE ONLY for this session.

The synthetic battery (``battery.run``) degrades generated fragments; the *field*
battery (spec s11 L3) needs real printed-and-photographed surfaces. This module
is the seam for that: given a folder of photos and a manifest describing each
one, it runs the IDENTICAL recognise pipeline and writes the SAME
``raw_results.csv`` shape, so synthetic and real rows are directly comparable.

Print-and-photograph capture is explicitly out of scope this session; here the
plumbing is defined and unit-tested with a generated PNG standing in for a photo.

Manifest format (``manifest.yaml``)::

    surface_id: bar-cascade-001        # optional default for every entry
    photos:
      - file: shot_0001.jpg            # path relative to the photo folder
        surface_id: bar-cascade-001    # which enrolled surface this depicts
        conditions:                    # free-form capture metadata (logged)
          light: office
          angle_deg: 15
          print: laser

Each photo becomes one CSV row: the recogniser's verdict + three numbers against
bar-cascade-001, plus the recorded capture conditions. Ground-truth fields that
only a generated fragment can know (``boundaries_spanned``, ``frac``) stay blank.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import yaml

from recogniser.claim import recognise
from battery.run import CSV_FIELDS, GRAMMARS, TARGET_SHEET_ID, _result_for, _feature_agreement


def _row_from_claim(file_name, surface_id, conditions, claim) -> dict:
    """One CSV row (battery.run.CSV_FIELDS shape) for a recognised real photo."""
    top = claim["results"][0]
    r001 = _result_for(claim, TARGET_SHEET_ID)
    cond_str = ";".join(f"{k}={v}" for k, v in sorted((conditions or {}).items()))
    row = {k: "" for k in CSV_FIELDS}
    row.update({
        "arm": "real",
        "impostor_id": "",
        "surface_seed": surface_id or "",
        "degradation": "photo",
        "degradation_param": cond_str,
        "top_sheet": top["sheet_id"],
        "top_aggregate": top["aggregate_confidence"],
        "verdict_001": r001["verdict"] if r001 else "",
        "aggregate_001": r001["aggregate_confidence"] if r001 else "",
        "coverage_001": r001["coverage"] if r001 else "",
        "renormalised_001": r001["renormalised_score"] if r001 else "",
        "agr_cascade_ratio": _feature_agreement(r001, "cascade_ratio") if r001 else "",
        "agr_duty": _feature_agreement(r001, "duty") if r001 else "",
        "agr_phase_duty_identity": _feature_agreement(r001, "phase_duty_identity") if r001 else "",
        "agr_colour_pair": _feature_agreement(r001, "colour_pair") if r001 else "",
    })
    row["rotation_deg"] = (conditions or {}).get("angle_deg", "")
    return row


def ingest(folder, manifest_path, out_csv=None, grammars_dir=None) -> list[dict]:
    """Recognise every photo listed in ``manifest_path`` under ``folder``.

    Runs the identical ``recognise`` pipeline used by the synthetic battery and
    (when ``out_csv`` is given) writes rows in the same ``raw_results.csv`` shape.
    Returns the list of row dicts. Raises ``FileNotFoundError`` for a missing
    photo so a broken manifest fails loudly rather than silently skipping.
    """
    folder = Path(folder)
    with open(manifest_path) as fh:
        manifest = yaml.safe_load(fh) or {}
    default_surface = manifest.get("surface_id")
    grammars = str(grammars_dir) if grammars_dir else str(GRAMMARS)

    rows = []
    for entry in manifest.get("photos", []):
        file_name = entry["file"]
        path = folder / file_name
        if not path.exists():
            raise FileNotFoundError(f"photo listed in manifest not found: {path}")
        surface_id = entry.get("surface_id", default_surface)
        conditions = entry.get("conditions", {})
        claim = recognise(str(path), grammars)
        rows.append(_row_from_claim(file_name, surface_id, conditions, claim))

    if out_csv is not None:
        with open(out_csv, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    return rows


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest photographed surfaces and recognise them (Phase 4b).")
    parser.add_argument("folder", help="folder of photo files")
    parser.add_argument("manifest", help="manifest.yaml listing the photos")
    parser.add_argument("--out", default=None, help="output raw_results.csv path")
    args = parser.parse_args(argv)
    rows = ingest(args.folder, args.manifest, out_csv=args.out)
    print(f"ingested {len(rows)} photo(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
