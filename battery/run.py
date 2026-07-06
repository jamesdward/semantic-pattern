"""Synthetic test-battery harness (Phase 4a) -- the empirical machinery for L3.

Spec s11 makes L3 ("Field-proven") conformance depend on a hostile-conditions
battery whose *criteria are deliberately unfinished until the data exists*. This
module produces that data synthetically: it drives the identical production
pipeline the recogniser uses --

    render surface (grammars/bar-cascade-001.yaml)
        -> sample a ground-truthed fragment (generator.fragments)
        -> degrade it (battery.degrade)
        -> recognise against ALL sheets in grammars/ (recogniser.claim.recognise)

-- across a declared sweep of fragment fractions, rotations and degradation
axes, plus an IMPOSTOR arm (non-enrolled plausible look-alikes) that tests the
false-positive discipline. Every fragment is one row of ``raw_results.csv``; the
run is summarised by curves (battery.chart) and a ``manifest.yaml`` recording the
git commit, config, sheet versions, seeds and timestamp for exact reproduction.

Design decisions worth stating:
  * The full grid is kept TRACTABLE by sweeping each degradation axis SEPARATELY
    against the fraction sweep at rotation 0 (not the full cross-product of every
    axis), and the rotation axis separately at no degradation. Cross-effects are
    out of scope for v0; the report says so.
  * Determinism: every fragment's crop (and its seeded perspective warp) is drawn
    from an ``np.random.SeedSequence`` keyed by (base_seed, arm, axis, param,
    frac, rotation, fragment index), so a whole run is byte-reproducible from the
    config alone (README principle 4). datetime is read ONCE, outside the sweep,
    and recorded in the manifest.
  * Impostors are rendered from in-memory MODIFIED COPIES of the 001 sheet dict,
    deliberately bypassing sheet validation -- they are impostors, they are not
    meant to be valid enrolled grammars. They keep 001's inks so the arm isolates
    which *structural* features leak (colour is held near-identical on purpose).
"""

from __future__ import annotations

import argparse
import copy
import csv
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from sheets import load_sheet, list_sheets
from generator import cascade
from generator.fragments import sample_fragment
from recogniser.claim import recognise
from recogniser import score as _score
from battery import degrade
from battery import chart

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAMMARS = REPO_ROOT / "grammars"
SHEET_001 = GRAMMARS / "bar-cascade-001.yaml"
TARGET_SHEET_ID = "bar-cascade-001"

# The 001 identification/normalisation features whose per-fragment agreement we
# log (the columns the report reads back).
FEATURE_COLUMNS = ["cascade_ratio", "duty", "phase_duty_identity", "colour_pair"]

CSV_FIELDS = [
    "arm", "impostor_id", "surface_seed", "frac", "rotation_deg",
    "degradation", "degradation_param", "boundaries_spanned",
    "band_first", "band_last", "top_sheet", "top_aggregate",
    "verdict_001", "aggregate_001", "coverage_001", "renormalised_001",
    "agr_cascade_ratio", "agr_duty", "agr_phase_duty_identity", "agr_colour_pair",
]


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

@dataclass
class BatteryConfig:
    """Declares the whole sweep. Defaults are the real (publishable) run.

    ``frags_per_cell`` is PER surface seed, so a (frac, rotation) or (frac, axis,
    param) cell holds ``frags_per_cell * len(surface_seeds)`` fragments. With the
    defaults that is 8 * 3 = 24 (>= 20 for stable per-cell statistics, as asked).
    """
    n_bands: int = 5
    module_px: int = 200
    surface_seeds: tuple = (0, 1, 2)
    frags_per_cell: int = 8

    fracs: tuple = (0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0)
    rotations: tuple = (0, 7, 30, 90)

    blur_sigmas: tuple = (0.0, 1.0, 2.0, 4.0)
    jpeg_qualities: tuple = (95, 75, 50, 30)
    brightness_scales: tuple = (0.7, 1.0, 1.3)
    # (label, (b, g, r) gains) -- a neutral control plus a warm and a cool cast.
    wb_sets: tuple = (
        ("neutral", (1.0, 1.0, 1.0)),
        ("warm", (0.85, 1.0, 1.15)),
        ("cool", (1.15, 1.0, 0.85)),
    )
    perspective_strengths: tuple = (0.0, 0.02, 0.05)

    # Fragment fractions used for the degradation and impostor arms (a subset of
    # ``fracs`` keeps those arms tractable while still spanning weak->whole).
    degradation_fracs: tuple = (0.1, 0.2, 0.35, 0.5, 0.75, 1.0)
    impostor_fracs: tuple = (0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0)

    base_seed: int = 20260705
    quick: bool = False


def quick_config() -> BatteryConfig:
    """A tiny smoke config for CI / tests (seconds, not minutes)."""
    return BatteryConfig(
        module_px=160,
        surface_seeds=(0,),
        frags_per_cell=2,
        fracs=(0.1, 0.5, 1.0),
        rotations=(0, 30),
        blur_sigmas=(0.0, 2.0),
        jpeg_qualities=(75,),
        brightness_scales=(1.0,),
        wb_sets=(("neutral", (1.0, 1.0, 1.0)),),
        perspective_strengths=(0.0,),
        degradation_fracs=(0.1, 0.5, 1.0),
        impostor_fracs=(0.1, 0.5, 1.0),
        quick=True,
    )


# --------------------------------------------------------------------------- #
# Impostors (in-memory modified copies of the 001 sheet dict; unvalidated)
# --------------------------------------------------------------------------- #

def build_impostors(genuine_sheet: dict) -> list[dict]:
    """Return a list of impostor specs: non-enrolled, plausible look-alikes.

    Each spec is ``{"id", "sheet", "n_bands"}`` where ``sheet`` is a MODIFIED
    COPY of the genuine 001 sheet (validation deliberately bypassed -- impostors
    are not meant to be valid grammars). Colours are held at 001's inks so the
    arm isolates which structural features leak into a false positive; the report
    diagnoses the leak. The canonical-peak impostors sit exactly on the crowded
    coordinates the audit (001 s3) warns about.
    """
    impostors = []

    def make(cr, duty, phase, n_bands, iid):
        s = copy.deepcopy(genuine_sheet)
        s["combination_rules"]["frequency_ratio"] = cr
        s["combination_rules"]["duty_cycle_light"] = duty
        s["combination_rules"]["phase_step"] = phase
        return {"id": iid, "sheet": s, "n_bands": n_bands}

    # 1. Canonical peak, sharing 001's phase == duty relation by design.
    impostors.append(make(2.0, 1.0 / 3.0, 1.0 / 3.0, 5, "canonical_ratio2_duty33_phase33"))
    # 2. Canonical peak, duty 1/2 / phase 1/2 (phase == duty at a different value).
    impostors.append(make(2.0, 0.5, 0.5, 5, "ratio2_duty50_phase50"))
    # 3. Ratio 1.5 (a canonical peak) with duty 1/3.
    impostors.append(make(1.5, 1.0 / 3.0, 1.0 / 3.0, 5, "ratio15_duty33"))
    # 4. Plain uniform stripe: a single period, NO cascade (n_bands = 1).
    impostors.append(make(2.0, 0.5, 0.5, 1, "uniform_stripe_no_cascade"))
    return impostors


# --------------------------------------------------------------------------- #
# Sweep primitives
# --------------------------------------------------------------------------- #

def _rng(config: BatteryConfig, *ints) -> np.random.Generator:
    """Deterministic Generator keyed by the config base seed + integer path."""
    return np.random.default_rng(np.random.SeedSequence([config.base_seed, *ints]))


def _degradation_cells(config: BatteryConfig):
    """Yield (axis, param_label, apply_fn) for every degradation-sweep cell.

    ``apply_fn(image, seed)`` applies the fixed-parameter degradation; ``seed``
    (the fragment's seed) makes the seeded perspective warp deterministic and the
    other (already-deterministic) axes ignore it.
    """
    for sigma in config.blur_sigmas:
        yield "blur", f"{sigma:g}", (lambda img, seed, s=sigma: degrade.gaussian_blur(img, s))
    for q in config.jpeg_qualities:
        yield "jpeg", f"{q}", (lambda img, seed, qq=q: degrade.jpeg_roundtrip(img, qq))
    for sc in config.brightness_scales:
        yield "brightness", f"{sc:g}", (lambda img, seed, s=sc: degrade.brightness(img, s))
    for label, gains in config.wb_sets:
        yield "white_balance", label, (lambda img, seed, g=gains: degrade.white_balance(img, g))
    for st in config.perspective_strengths:
        yield "perspective", f"{st:g}", (lambda img, seed, s=st: degrade.perspective_warp(img, s, seed))


def _feature_agreement(result: dict, fid: str):
    """Return the per-feature agreement for ``fid`` in an 001 score result, or None."""
    for f in result["per_feature"]:
        if f["id"] == fid:
            return f.get("agreement")
    return None


def _result_for(claim: dict, sheet_id: str):
    for r in claim["results"]:
        if r["sheet_id"] == sheet_id:
            return r
    return None


def _row(arm, impostor_id, surface_seed, frac, rotation, degradation,
         param, info, claim) -> dict:
    """Assemble one CSV row from a fragment's ground truth + its recognise claim."""
    top = claim["results"][0]
    r001 = _result_for(claim, TARGET_SHEET_ID)
    span = info.band_span or (None, None)
    return {
        "arm": arm,
        "impostor_id": impostor_id or "",
        "surface_seed": surface_seed,
        "frac": round(float(frac), 4),
        "rotation_deg": float(rotation),
        "degradation": degradation,
        "degradation_param": param,
        "boundaries_spanned": info.band_boundaries_spanned,
        "band_first": span[0],
        "band_last": span[1],
        "top_sheet": top["sheet_id"],
        "top_aggregate": top["aggregate_confidence"],
        "verdict_001": r001["verdict"],
        "aggregate_001": r001["aggregate_confidence"],
        "coverage_001": r001["coverage"],
        "renormalised_001": r001["renormalised_score"],
        "agr_cascade_ratio": _feature_agreement(r001, "cascade_ratio"),
        "agr_duty": _feature_agreement(r001, "duty"),
        "agr_phase_duty_identity": _feature_agreement(r001, "phase_duty_identity"),
        "agr_colour_pair": _feature_agreement(r001, "colour_pair"),
    }


def _sample_and_recognise(surface, frac, rotation, config, module_px, n_bands,
                          seed_path):
    """Sample one fragment (returns None on an impossible rotated geometry)."""
    rng = _rng(config, *seed_path)
    try:
        frag, info = sample_fragment(
            surface, frac=frac, rng=rng, rotation_deg=float(rotation),
            module_px=module_px, n_bands=n_bands,
        )
    except ValueError:
        # Rotated window does not fit the surface (large frac x rotation): a
        # geometric impossibility, honestly skipped and tallied.
        return None, None
    # DEFENSIVE ONLY as of the Phase-7 sampler fix (SI-017 gap 2 closed): the
    # rotated crop used to degenerate to a zero-size window near 90 deg under
    # aspect jitter, and this guard skipped+tallied those. sample_fragment now
    # frames the warp directly onto the w x h window, so it can no longer return
    # an empty array; the check is kept as a cheap invariant (never fires) rather
    # than removed, so this frozen exp-001 harness stays byte-reproducible.
    if frag.size == 0 or 0 in frag.shape:
        return None, None
    return frag, info


# --------------------------------------------------------------------------- #
# Main run
# --------------------------------------------------------------------------- #

def run_battery(config: BatteryConfig, out_dir, timestamp: str,
                progress=True) -> dict:
    """Execute the whole battery, write all artifacts under ``out_dir``.

    Returns a small summary dict (row count, skip count, artifact paths). All
    determinism/timestamp inputs come from ``config`` / ``timestamp`` so the run
    is reproducible; the timestamp is captured by the CALLER (outside any sweep).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    genuine_sheet = load_sheet(SHEET_001)
    n_bands, module_px = config.n_bands, config.module_px

    rows: list[dict] = []
    skips = 0

    # Pre-render one genuine surface per seed (reused across every genuine cell).
    genuine_surfaces = {
        s: cascade.render(genuine_sheet, n_bands=n_bands, module_px=module_px,
                          seed=s, orientation_deg=0.0)
        for s in config.surface_seeds
    }

    def emit(msg):
        if progress:
            print(msg, file=sys.stderr, flush=True)

    # --- Arm 1: rotation x fraction sweep, no degradation --------------------
    emit("arm 1/3: rotation x fraction (genuine, no degradation)")
    for ri, rotation in enumerate(config.rotations):
        for fi, frac in enumerate(config.fracs):
            for si, seed in enumerate(config.surface_seeds):
                surface = genuine_surfaces[seed]
                for k in range(config.frags_per_cell):
                    frag, info = _sample_and_recognise(
                        surface, frac, rotation, config, module_px, n_bands,
                        (1, ri, fi, si, k))
                    if frag is None:
                        skips += 1
                        continue
                    claim = recognise(frag, str(GRAMMARS))
                    rows.append(_row("genuine", None, seed, frac, rotation,
                                     "none", "", info, claim))

    # --- Arm 2: degradation axes x fraction sweep, rotation 0 ----------------
    emit("arm 2/3: degradation axes x fraction (genuine, rotation 0)")
    for ai, (axis, param, apply_fn) in enumerate(_degradation_cells(config)):
        for fi, frac in enumerate(config.degradation_fracs):
            for si, seed in enumerate(config.surface_seeds):
                surface = genuine_surfaces[seed]
                for k in range(config.frags_per_cell):
                    frag, info = _sample_and_recognise(
                        surface, frac, 0, config, module_px, n_bands,
                        (2, ai, fi, si, k))
                    if frag is None:
                        skips += 1
                        continue
                    seed_int = int(np.random.SeedSequence(
                        [config.base_seed, 2, ai, fi, si, k]).generate_state(1)[0])
                    degraded = apply_fn(frag, seed_int)
                    claim = recognise(degraded, str(GRAMMARS))
                    rows.append(_row("genuine", None, seed, frac, 0,
                                     axis, param, info, claim))

    # --- Arm 3: impostors x fraction sweep, rotation 0, no degradation -------
    emit("arm 3/3: impostors x fraction (false-positive check)")
    impostors = build_impostors(genuine_sheet)
    for ii, imp in enumerate(impostors):
        imp_surfaces = {
            s: cascade.render(imp["sheet"], n_bands=imp["n_bands"],
                              module_px=module_px, seed=s, orientation_deg=0.0,
                              size=(n_bands * module_px, n_bands * module_px))
            for s in config.surface_seeds
        }
        for fi, frac in enumerate(config.impostor_fracs):
            for si, seed in enumerate(config.surface_seeds):
                surface = imp_surfaces[seed]
                # Uniform-stripe impostor has one band, so band ground truth uses
                # its own n_bands; boundaries_spanned stays honest.
                for k in range(config.frags_per_cell):
                    frag, info = _sample_and_recognise(
                        surface, frac, 0, config, module_px, imp["n_bands"],
                        (3, ii, fi, si, k))
                    if frag is None:
                        skips += 1
                        continue
                    claim = recognise(frag, str(GRAMMARS))
                    rows.append(_row("impostor", imp["id"], seed, frac, 0,
                                     "none", "", info, claim))

    emit(f"collected {len(rows)} fragment rows ({skips} skipped for geometry)")

    # --- write raw_results.csv ----------------------------------------------
    csv_path = out_dir / "raw_results.csv"
    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    # --- manifest.yaml -------------------------------------------------------
    sheets = list_sheets(GRAMMARS)
    manifest = {
        "experiment": "exp-001-synthetic-battery",
        "timestamp": timestamp,
        "git_commit": _git_commit(),
        "command": "python -m battery.run " + " ".join(sys.argv[1:]),
        "recogniser_version": "v0",
        "verdict_thresholds": {
            "identified": _score.IDENTIFIED_THRESHOLD,
            "candidate": _score.CANDIDATE_THRESHOLD,
        },
        "config": asdict(config),
        "surface_seeds": list(config.surface_seeds),
        "sheets": [
            {"id": s["sheet"]["id"],
             "grammar_version": s["sheet"]["grammar_version"],
             "status": s["sheet"]["status"]}
            for s in sheets
        ],
        "impostors": [
            {"id": imp["id"], "n_bands": imp["n_bands"],
             "frequency_ratio": imp["sheet"]["combination_rules"]["frequency_ratio"],
             "duty_cycle_light": imp["sheet"]["combination_rules"]["duty_cycle_light"],
             "phase_step": imp["sheet"]["combination_rules"]["phase_step"],
             "note": "rendered from an in-memory MODIFIED copy of the 001 sheet; "
                     "validation bypassed (impostor, not a valid grammar)"}
            for imp in impostors
        ],
        "rows": len(rows),
        "skipped_geometry": skips,
        "csv": "raw_results.csv",
    }
    manifest_path = out_dir / "manifest.yaml"
    with open(manifest_path, "w") as fh:
        yaml.safe_dump(manifest, fh, sort_keys=False)

    # --- charts --------------------------------------------------------------
    charts = _make_charts(rows, config, out_dir)

    return {
        "rows": len(rows),
        "skipped": skips,
        "csv": str(csv_path),
        "manifest": str(manifest_path),
        "charts": [str(c) for c in charts],
    }


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), text=True
        ).strip()
    except Exception:
        return "unknown"


# --------------------------------------------------------------------------- #
# Chart assembly (all numbers recomputed from the collected rows)
# --------------------------------------------------------------------------- #

def _mean_band(values):
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    if arr.size == 0:
        return None, None
    return float(arr.mean()), float(arr.std())


def _make_charts(rows, config: BatteryConfig, out_dir) -> list:
    paths = []
    genuine_none = [r for r in rows if r["arm"] == "genuine" and r["degradation"] == "none"]

    # 1. minimum-fragment-vs-confidence, one line per rotation ----------------
    fracs = list(config.fracs)
    series = {}
    for rotation in config.rotations:
        means, bands = [], []
        for frac in fracs:
            vals = [r["aggregate_001"] for r in genuine_none
                    if r["rotation_deg"] == float(rotation)
                    and abs(r["frac"] - frac) < 1e-6]
            m, b = _mean_band(vals)
            means.append(m)
            bands.append(b)
        series[f"rot {rotation} deg"] = {"mean": means, "band": bands}
    paths.append(chart.line_chart(
        out_dir / "curve_min_fragment_vs_confidence.png", series, fracs,
        xlabel="fragment fraction of surface area",
        ylabel="mean aggregate confidence (001)",
        title="Minimum fragment vs confidence (by rotation)",
        hlines=[(_score.IDENTIFIED_THRESHOLD, "identified 0.70"),
                (_score.CANDIDATE_THRESHOLD, "candidate 0.40")]))

    # 1b. split by boundaries spanned (rotation 0) -- shows the SI-014 plateau -
    buckets = {"0 boundaries": lambda n: n == 0,
               "1 boundary": lambda n: n == 1,
               "2+ boundaries": lambda n: n is not None and n >= 2}
    rot0 = [r for r in genuine_none if r["rotation_deg"] == 0.0]
    series2 = {}
    for label, pred in buckets.items():
        means, bands = [], []
        for frac in fracs:
            vals = [r["aggregate_001"] for r in rot0
                    if abs(r["frac"] - frac) < 1e-6 and pred(r["boundaries_spanned"])]
            m, b = _mean_band(vals)
            means.append(m)
            bands.append(b)
        series2[label] = {"mean": means, "band": bands}
    paths.append(chart.line_chart(
        out_dir / "curve_confidence_by_boundaries.png", series2, fracs,
        xlabel="fragment fraction of surface area",
        ylabel="mean aggregate confidence (001)",
        title="Confidence by band boundaries spanned (rotation 0)",
        hlines=[(_score.IDENTIFIED_THRESHOLD, "identified 0.70"),
                (_score.CANDIDATE_THRESHOLD, "candidate 0.40")]))

    # 2. degradation curves, one chart per axis -------------------------------
    dfracs = list(config.degradation_fracs)
    axes_params = {}
    for r in rows:
        if r["arm"] == "genuine" and r["degradation"] != "none":
            axes_params.setdefault(r["degradation"], set()).add(r["degradation_param"])
    for axis, params in axes_params.items():
        series_d = {}
        for param in sorted(params, key=_param_sort_key):
            means, bands = [], []
            for frac in dfracs:
                vals = [r["aggregate_001"] for r in rows
                        if r["arm"] == "genuine" and r["degradation"] == axis
                        and r["degradation_param"] == param
                        and abs(r["frac"] - frac) < 1e-6]
                m, b = _mean_band(vals)
                means.append(m)
                bands.append(b)
            series_d[f"{axis}={param}"] = {"mean": means, "band": bands}
        paths.append(chart.line_chart(
            out_dir / f"curve_degradation_{axis}.png", series_d, dfracs,
            xlabel="fragment fraction of surface area",
            ylabel="mean aggregate confidence (001)",
            title=f"Degradation tolerance: {axis}",
            hlines=[(_score.IDENTIFIED_THRESHOLD, "identified 0.70"),
                    (_score.CANDIDATE_THRESHOLD, "candidate 0.40")]))

    # 3. impostor vs genuine confidence distributions -------------------------
    # Short legend labels keep the long impostor ids legible on the chart; the
    # full ids stay in raw_results.csv, the manifest and the report.
    short = {
        "canonical_ratio2_duty33_phase33": "imp1 r2.0 duty.33 ph.33",
        "ratio2_duty50_phase50": "imp2 r2.0 duty.50 ph.50",
        "ratio15_duty33": "imp3 r1.5 duty.33",
        "uniform_stripe_no_cascade": "imp4 uniform stripe",
    }
    dist = {"genuine": [r["aggregate_001"] for r in genuine_none]}
    for imp_id in sorted({r["impostor_id"] for r in rows if r["arm"] == "impostor"}):
        label = short.get(imp_id, imp_id)
        dist[label] = [r["aggregate_001"] for r in rows
                       if r["arm"] == "impostor" and r["impostor_id"] == imp_id]
    paths.append(chart.hist_chart(
        out_dir / "dist_impostor_vs_genuine.png", dist,
        xlabel="aggregate confidence against bar-cascade-001",
        title="Impostor vs genuine confidence distribution",
        vlines=[(_score.IDENTIFIED_THRESHOLD, "0.70"),
                (_score.CANDIDATE_THRESHOLD, "0.40")]))

    return paths


def _param_sort_key(p):
    try:
        return (0, float(p))
    except (TypeError, ValueError):
        return (1, p)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the synthetic recognition test battery.")
    parser.add_argument("--out", default="experiments/exp-001-synthetic-battery",
                        help="output directory for artifacts")
    parser.add_argument("--quick", action="store_true",
                        help="tiny smoke config (CI/tests)")
    args = parser.parse_args(argv)

    config = quick_config() if args.quick else BatteryConfig()
    timestamp = datetime.now(timezone.utc).isoformat()
    summary = run_battery(config, args.out, timestamp)
    print(f"wrote {summary['rows']} rows to {summary['csv']}")
    print(f"manifest: {summary['manifest']}")
    for c in summary["charts"]:
        print(f"chart: {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
