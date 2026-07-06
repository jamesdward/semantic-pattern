"""Phase-4b real-photo ingestion path -- the field half of the L3 battery.

The synthetic battery (``battery.run``) degrades generated fragments; the *field*
battery (spec s11 L3: "print, camera, light, angle, damage") needs real
printed-and-photographed surfaces. This module is that path: given a folder of
photos and a manifest describing each one, it runs the IDENTICAL recognise
pipeline the synthetic battery uses -- **no photo-special preprocessing in v0**.
If recognition fails on a raw phone photo, that is a RESULT to record and report
(the summary marks it), never something to silently fix here; a v0 that quietly
pre-corrected photos could not tell us what real capture does to recognition,
which is the whole question (spec s11: "criteria deliberately unfinished until
that data exists").

Each photo becomes one row of ``raw_results.csv``: the manifest fields verbatim,
a per-row status, the recogniser's top verdict, the target-sheet aggregate /
coverage / renormalised / verdict, the four bar-cascade-001 feature agreements
(for 001 rows), and -- for iso-002 rows -- the ink two-path detail INCLUDING the
implied applied gain (1/g). That gain block is what answers Phase-4b question (a):
the relationship colour path (SI-020) assumes a real illuminant is a per-channel
DIAGONAL gain; the implied applied gain recovers the white balance it removed, and
``ink_gain_in_bounds`` / ``ink_rank_correlation`` say whether a single diagonal
gain actually explained the cast. If those are consistently out of bounds or the
rank correlation is low across real photos, the diagonal model is wrong.

A ``summary.md`` is auto-written alongside: per-condition tables plus the three
Phase-4b questions (from ``experiments/exp-002-cross-grammar`` s6), each answered
from the data present or explicitly marked "insufficient data".

Robustness (a broken capture set must never crash the run): a manifest entry
whose file is missing, a file in the folder not named in the manifest, and an
unreadable/corrupt image are each recorded as a row ``status`` -- never an
exception. Only a malformed manifest file (not loadable YAML) is fatal.

Manifest format (``manifest.yaml``)::

    surface_id: null                 # optional default surface_id for every entry
    photos:
      - file: IMG_0001.jpg           # filename inside the photo folder
        surface_id: 001-s0           # matches the printed label (printpack)
        grammar: bar-cascade-001     # bar-cascade-001 | iso-002 (the target sheet)
        conditions:
          lighting: daylight
          angle_deg: 0
          distance: fills_frame
          printer: "Brand Model"
          paper: plain
        notes: ""
"""

from __future__ import annotations

import argparse
import csv
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from recogniser.claim import recognise

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GRAMMARS = REPO_ROOT / "grammars"

# Recognised image extensions when scanning the folder for stray files.
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic"}

# The four bar-cascade-001 identification/normalisation feature agreements we log
# (mirrors battery.run.FEATURE_COLUMNS so 001 real rows line up with synthetic).
BAND_FEATURES = ["cascade_ratio", "duty", "phase_duty_identity", "colour_pair"]

# Manifest condition keys logged verbatim (order fixes the CSV column order).
CONDITION_KEYS = ["lighting", "angle_deg", "distance", "medium", "printer", "paper"]

CSV_FIELDS = [
    # provenance + status
    "file", "status", "surface_id", "grammar", "notes",
    *CONDITION_KEYS,
    # recognition summary
    "image_shape", "top_sheet", "top_aggregate",
    "target_sheet", "verdict", "aggregate", "coverage", "renormalised",
    # bar-cascade-001 per-feature agreements (blank for grid rows)
    "agr_cascade_ratio", "agr_duty", "agr_phase_duty_identity", "agr_colour_pair",
    # iso-002 ink two-path detail incl. implied applied gain (blank for band rows)
    "ink_agreement_absolute", "ink_agreement_relationship",
    "ink_mean_delta_e_absolute", "ink_mean_delta_e_relationship",
    "ink_correction_gain_b", "ink_correction_gain_g", "ink_correction_gain_r",
    "ink_implied_applied_gain_b", "ink_implied_applied_gain_g",
    "ink_implied_applied_gain_r",
    "ink_gain_in_bounds", "ink_relationship_applicable",
    "ink_n_inks", "ink_n_clipped_inks", "ink_gain_fallback_channels",
    "ink_rank_correlation",
    # anything that went wrong (unreadable image message, etc.)
    "error",
]


# --------------------------------------------------------------------------- #
# Claim -> row extraction
# --------------------------------------------------------------------------- #

def _result_for(claim: dict, sheet_id: str):
    for r in claim.get("results", []):
        if r.get("sheet_id") == sheet_id:
            return r
    return None


def _feature_agreement(result: dict, fid: str):
    if not result:
        return None
    for f in result.get("per_feature", []):
        if f.get("id") == fid:
            return f.get("agreement")
    return None


def _ink_two_path_detail(result: dict):
    """Return the grid ink-set two-path ``detail`` dict for ``result`` or None.

    Scans the per-feature working for the ``ink_set_match`` measure carrying the
    white-balance relationship path (a ``correction_gain_bgr`` marks the grid
    two-path detail; band sheets score colour absolute-only and have no gain).
    """
    if not result:
        return None
    for f in result.get("per_feature", []):
        if f.get("measure") == "ink_set_match":
            detail = f.get("detail") or {}
            if "correction_gain_bgr" in detail:
                return detail
    return None


def _base_row() -> dict:
    return {k: "" for k in CSV_FIELDS}


def _row_from_claim(entry_row: dict, claim: dict, target_sheet: str) -> dict:
    """Fill ``entry_row`` (already carrying manifest fields) from a recognise claim."""
    row = dict(entry_row)
    row["status"] = "ok"
    row["image_shape"] = "x".join(str(d) for d in claim.get("image_shape", []))
    results = claim.get("results", [])
    if results:
        row["top_sheet"] = results[0].get("sheet_id", "")
        row["top_aggregate"] = results[0].get("aggregate_confidence", "")

    target = _result_for(claim, target_sheet) if target_sheet else None
    if target is None and results:
        # No declared grammar (or it is not an enrolled sheet): fall back to the
        # recogniser's own top result so the row still carries a verdict.
        target = results[0]
        row["target_sheet"] = target.get("sheet_id", "")
    else:
        row["target_sheet"] = target_sheet or ""

    if target:
        row["verdict"] = target.get("verdict", "")
        row["aggregate"] = target.get("aggregate_confidence", "")
        row["coverage"] = target.get("coverage", "")
        row["renormalised"] = target.get("renormalised_score", "")
        for fid in BAND_FEATURES:
            agr = _feature_agreement(target, fid)
            if agr is not None:
                row[f"agr_{fid}"] = agr

        detail = _ink_two_path_detail(target)
        if detail:
            gain = detail.get("correction_gain_bgr", [None, None, None])
            impl = detail.get("implied_applied_gain_bgr", [None, None, None])
            clip = detail.get("clipping", {})
            row.update({
                "ink_agreement_absolute": detail.get("agreement_absolute", ""),
                "ink_agreement_relationship": detail.get("agreement_relationship", ""),
                "ink_mean_delta_e_absolute": detail.get("mean_delta_e_absolute", ""),
                "ink_mean_delta_e_relationship": detail.get("mean_delta_e_relationship", ""),
                "ink_correction_gain_b": gain[0], "ink_correction_gain_g": gain[1],
                "ink_correction_gain_r": gain[2],
                "ink_implied_applied_gain_b": impl[0],
                "ink_implied_applied_gain_g": impl[1],
                "ink_implied_applied_gain_r": impl[2],
                "ink_gain_in_bounds": detail.get("gain_in_bounds", ""),
                "ink_relationship_applicable": detail.get("relationship_applicable", ""),
                "ink_n_inks": detail.get("n_inks", ""),
                "ink_n_clipped_inks": clip.get("n_clipped_inks", ""),
                "ink_gain_fallback_channels": ",".join(clip.get("gain_fallback_channels", [])),
                "ink_rank_correlation": detail.get("rank_correlation", ""),
            })
    return row


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #

def _entry_row(entry: dict, default_surface: str) -> dict:
    """Build the manifest-field portion of a row (no recognition yet)."""
    row = _base_row()
    row["file"] = entry.get("file", "")
    row["surface_id"] = entry.get("surface_id", default_surface) or ""
    row["grammar"] = entry.get("grammar", "") or ""
    row["notes"] = entry.get("notes", "") or ""
    conditions = entry.get("conditions") or {}
    for k in CONDITION_KEYS:
        v = conditions.get(k, "")
        row[k] = "" if v is None else v
    return row


def ingest(folder, manifest_path, out_dir=None, *, grammars_dir=None,
           timestamp=None) -> list[dict]:
    """Recognise every photo in ``manifest_path`` under ``folder``; return rows.

    Runs the identical ``recognise`` pipeline used by the synthetic battery. When
    ``out_dir`` is given, writes ``raw_results.csv`` and ``summary.md`` there.

    A missing photo, a folder file absent from the manifest, and an unreadable
    image are each recorded as a row ``status`` (``missing`` / ``not_in_manifest``
    / ``unreadable``) rather than raising -- a broken capture set never crashes
    the run. A manifest that will not parse as YAML IS fatal (that is operator
    error, not capture data).
    """
    folder = Path(folder)
    grammars = str(grammars_dir) if grammars_dir else str(GRAMMARS)
    with open(manifest_path) as fh:
        manifest = yaml.safe_load(fh) or {}
    default_surface = manifest.get("surface_id")

    rows: list[dict] = []
    listed_files = set()
    for entry in manifest.get("photos", []) or []:
        entry_row = _entry_row(entry, default_surface)
        file_name = entry_row["file"]
        listed_files.add(file_name)
        target_sheet = entry_row["grammar"]
        path = folder / file_name if file_name else None

        if not file_name:
            entry_row["status"] = "missing"
            entry_row["error"] = "manifest entry has no 'file'"
            rows.append(entry_row)
            continue
        if path is None or not path.exists():
            entry_row["status"] = "missing"
            entry_row["error"] = f"file not found under {folder}"
            rows.append(entry_row)
            continue
        try:
            claim = recognise(str(path), grammars)
        except Exception as exc:  # unreadable/corrupt image, decode failure, etc.
            entry_row["status"] = "unreadable"
            entry_row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(entry_row)
            continue
        rows.append(_row_from_claim(entry_row, claim, target_sheet))

    # Files present in the folder but never named in the manifest -> flagged.
    for p in sorted(folder.glob("*")) if folder.exists() else []:
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS and p.name not in listed_files:
            stray = _base_row()
            stray["file"] = p.name
            stray["status"] = "not_in_manifest"
            stray["error"] = "image in folder not listed in manifest"
            rows.append(stray)

    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "raw_results.csv", "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        (out_dir / "summary.md").write_text(build_summary(rows, ts))

    return rows


# --------------------------------------------------------------------------- #
# summary.md
# --------------------------------------------------------------------------- #

def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), text=True).strip()
    except Exception:
        return "unknown"


def _num(v):
    """Coerce a CSV cell to float, or None if blank/non-numeric."""
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ok_rows(rows):
    return [r for r in rows if r.get("status") == "ok"]


def _fmt(v, nd=3):
    return "--" if v is None else f"{v:.{nd}f}"


def _mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _verdict_counts(rows):
    counts = {"identified": 0, "candidate": 0, "not_recognised": 0, "other": 0}
    for r in rows:
        v = r.get("verdict", "")
        counts[v if v in counts else "other"] += 1
    return counts


def _condition_table(rows, key, title):
    """A markdown table of mean aggregate + verdict mix grouped by one condition."""
    groups: dict = {}
    for r in rows:
        groups.setdefault(str(r.get(key, "")), []).append(r)
    lines = [f"**By {title}**", "",
             f"| {title} | n | mean aggregate | identified | candidate | not_recognised |",
             "|---|---|---|---|---|---|"]
    for g in sorted(groups):
        grp = groups[g]
        vc = _verdict_counts(grp)
        magg = _mean([_num(r.get("aggregate")) for r in grp])
        lines.append(f"| {g or '(blank)'} | {len(grp)} | {_fmt(magg)} | "
                     f"{vc['identified']} | {vc['candidate']} | {vc['not_recognised']} |")
    lines.append("")
    return "\n".join(lines)


def _answer_wb_diagonal(grid_rows):
    """Question (a): is real camera white balance diagonal?"""
    usable = [r for r in grid_rows
              if str(r.get("ink_relationship_applicable")).lower() == "true"]
    if not usable:
        return ("**(a) Is real camera white balance diagonal?** insufficient data "
                "-- no iso-002 photo recognised with the relationship colour path "
                "applicable (need >= 3 inks in frame; SI-020).")
    in_bounds = sum(1 for r in usable
                    if str(r.get("ink_gain_in_bounds")).lower() == "true")
    mean_rank = _mean([_num(r.get("ink_rank_correlation")) for r in usable])
    frac = in_bounds / len(usable)
    verdict = ("consistent with a per-channel diagonal gain"
               if frac >= 0.8 and (mean_rank or 0) >= 0.8
               else "NOT well explained by a diagonal gain (the SI-020 model is "
                    "too weak for real illuminants)")
    return (f"**(a) Is real camera white balance diagonal?** {verdict}. "
            f"{in_bounds}/{len(usable)} recognised iso-002 photos had an in-bounds "
            f"single diagonal correction gain (GAIN_MIN..GAIN_MAX), mean per-channel "
            f"rank correlation {_fmt(mean_rank)}. A positive diagonal gain is "
            f"order-preserving, so high rank correlation corroborates the diagonal "
            f"model; low values (or many out-of-bounds gains) say real white balance "
            f"is not diagonal and the relationship path needs a richer transform.")


def _answer_clipping(grid_rows):
    """Question (b): real clipping behaviour on bright inks."""
    counts = [_num(r.get("ink_n_clipped_inks")) for r in grid_rows]
    counts = [c for c in counts if c is not None]
    if not counts:
        return ("**(b) Real clipping on bright inks?** insufficient data -- no "
                "iso-002 photo produced a measured ink set to inspect for clipping.")
    total_clipped = sum(int(c) for c in counts)
    n_with_clip = sum(1 for c in counts if c > 0)
    return (f"**(b) Real clipping on bright inks?** across {len(counts)} recognised "
            f"iso-002 photos, {n_with_clip} showed >= 1 clipped ink "
            f"(mean {_fmt(_mean(counts), 2)} clipped inks/photo, {total_clipped} total). "
            f"Clipping destroys a bright ink's channel asymmetrically (only a lower "
            f"bound survives; SI-020), so a rising clipped-ink count under warm/bright "
            f"lighting is the s2 strong-warm edge appearing on real ink -- cross-check "
            f"against the by-lighting table above.")


def _answer_gamut(grid_rows):
    """Question (c): does 002's ink set survive the print gamut?"""
    if not grid_rows:
        return ("**(c) Does 002's ink set survive the print gamut?** insufficient "
                "data -- no iso-002 photo was successfully recognised.")
    vc = _verdict_counts(grid_rows)
    survived = vc["identified"] + vc["candidate"]
    mean_abs = _mean([_num(r.get("ink_agreement_absolute")) for r in grid_rows])
    mean_rel = _mean([_num(r.get("ink_agreement_relationship")) for r in grid_rows])
    return (f"**(c) Does 002's ink set survive the print gamut?** {survived}/"
            f"{len(grid_rows)} recognised iso-002 photos reached candidate or better "
            f"(identified {vc['identified']}, candidate {vc['candidate']}, "
            f"not_recognised {vc['not_recognised']}). Mean ink agreement: absolute "
            f"path {_fmt(mean_abs)}, relationship path {_fmt(mean_rel)}. If the "
            f"absolute path collapses but the relationship path holds, the print "
            f"shifted the inks by a recoverable cast; if BOTH collapse, the print "
            f"gamut moved the inks out of delta-E tolerance and 002's single "
            f"colour-borne identity (SI-008/SI-022) does not survive this print.")


def build_summary(rows, timestamp: str) -> str:
    """Render summary.md from the ingested rows (deterministic given ``timestamp``)."""
    ok = _ok_rows(rows)
    band_rows = [r for r in ok if r.get("grammar") == "bar-cascade-001"]
    grid_rows = [r for r in ok if r.get("grammar") == "iso-002"]

    status_counts: dict = {}
    for r in rows:
        status_counts[r.get("status", "")] = status_counts.get(r.get("status", ""), 0) + 1

    L = []
    L.append("# exp-003 print-and-photograph -- ingest summary")
    L.append("")
    L.append(f"- generated: {timestamp}")
    L.append(f"- git commit: {_git_commit()}")
    L.append(f"- recogniser: v0 (identical pipeline; no photo-special preprocessing)")
    L.append(f"- rows: {len(rows)}  "
             f"(" + ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items())) + ")")
    L.append(f"- recognised: bar-cascade-001 {len(band_rows)}, iso-002 {len(grid_rows)}")
    L.append("")

    if not ok:
        L.append("No photo was successfully recognised, so every Phase-4b question "
                 "below is answered **insufficient data**. Check the per-row status "
                 "in raw_results.csv (missing / unreadable / not_in_manifest).")
        L.append("")

    L.append("## Per-condition tables")
    L.append("")
    if ok:
        L.append(_condition_table(ok, "lighting", "lighting"))
        L.append(_condition_table(ok, "angle_deg", "angle (deg)"))
        L.append(_condition_table(ok, "distance", "distance"))
        L.append(_condition_table(ok, "surface_id", "surface"))
    else:
        L.append("_(no recognised rows to tabulate)_")
        L.append("")

    L.append("## The three Phase-4b questions (exp-002 s6)")
    L.append("")
    L.append(_answer_wb_diagonal(grid_rows))
    L.append("")
    L.append(_answer_clipping(grid_rows))
    L.append("")
    L.append(_answer_gamut(grid_rows))
    L.append("")
    L.append("---")
    L.append("")
    L.append("Each answer is computed only from photos that recognised; a question "
             "with no supporting rows is marked *insufficient data* rather than "
             "guessed. L3 (spec s11) stays open until this runs on a real, "
             "sufficiently-covered capture set (SI-017, SI-023).")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest photographed surfaces and recognise them (Phase 4b).")
    parser.add_argument("folder", help="folder of photo files")
    parser.add_argument("manifest", help="manifest.yaml listing the photos")
    parser.add_argument("--out", default=None,
                        help="output directory for raw_results.csv + summary.md")
    parser.add_argument("--grammars", default=None,
                        help="grammar sheet directory (default: repo grammars/)")
    args = parser.parse_args(argv)
    rows = ingest(args.folder, args.manifest, out_dir=args.out,
                  grammars_dir=args.grammars)
    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"ingested {len(rows)} row(s); {ok} recognised")
    if args.out:
        print(f"wrote {args.out}/raw_results.csv and {args.out}/summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
