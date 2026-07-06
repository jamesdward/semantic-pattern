"""Cross-grammar experiment harness (exp-002, Milestone 2 Phase 7).

Where ``battery.run`` (exp-001) proved the *band* recogniser identifies 001
surfaces under synthetic degradation, this harness answers the Milestone 2
questions that only appear once TWO grammar families are enrolled and scored side
by side (spec s5: "the false-positive rate against the corpus is a published
property of the grammar"):

  1. CROSS-GRAMMAR DISCRIMINATION. Does any 001 fragment ever reach candidate
     against 002, or vice versa? Every fragment is recognised against ALL sheets
     in ``grammars/`` and BOTH sheets' scores are recorded, so the margin between
     the true-sheet and cross-sheet confidence distributions is a measured,
     published number (charts: confidence-vs-frac per true grammar; confusion
     summary of verdict counts per true-grammar x scored-sheet).

  2. WHITE-BALANCE ROBUSTNESS (the Milestone 2 core claim, SI-020). 002's
     identification lives in its ink set (audit 002 s6), and colour-led signatures
     are print-fragile. The two-path grid ink match (absolute vs a single global
     white-balance-gain relationship) is swept over a neutral/warm/cool/strong-warm
     gain sweep at frac 0.5; the relationship path's survival curve vs the absolute
     path IS the claim, including where it breaks (strong-warm clipping, SI-020).

  3. IMPOSTORS, mutual. The exp-001 canonical band impostor (ratio 2.0/duty 1/3)
     AND a NINE-INK grid impostor (a 002-structured composition drawn in nine inks
     hue-rotated far from 002's master set) are scored against both sheets. The
     nine-ink impostor is the structure-only-leak test: everything canonical in
     002 is weight-0, so a grid that shares 002's structure but not its inks should
     score ~0 -- verified here, not assumed.

Design reuses exp-001 machinery (``battery.degrade``, ``battery.chart``,
``generator.fragments``, ``recogniser.claim``) and mirrors ``battery.run``'s
determinism discipline: every fragment's crop is drawn from an
``np.random.SeedSequence`` keyed by (base_seed, arm, ...); the timestamp is read
ONCE by the caller and recorded in the manifest; the same config yields a
byte-identical CSV and byte-identical chart PNGs (asserted in tests/test_cross.py).

Classical CV only, no learned models, no new dependencies (README principle 1/4).
"""

from __future__ import annotations

import argparse
import copy
import csv
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import yaml

from sheets import load_sheet, list_sheets
from generator import cascade
from generator import grid
from generator.fragments import sample_fragment
from recogniser.claim import recognise
from recogniser import score as _score
from battery import degrade
from battery import chart

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAMMARS = REPO_ROOT / "grammars"
SHEET_001 = GRAMMARS / "bar-cascade-001.yaml"
SHEET_002 = GRAMMARS / "iso-002.yaml"

SHEET_001_ID = "bar-cascade-001"
SHEET_002_ID = "iso-002"
SCORED_SHEETS = (SHEET_001_ID, SHEET_002_ID)


# --------------------------------------------------------------------------- #
# CSV schema
# --------------------------------------------------------------------------- #
# Per-fragment row: which grammar it truly is, the sweep coordinates, BOTH
# sheets' aggregate/coverage/verdict, and the 002 sheet's two-path ink detail
# (absolute vs relationship agreement, the estimated correction gain and its
# implied applied white balance) -- recorded for every row because 002 is always
# scored, so a 001 fragment's cross-grammar ink behaviour is captured too.
CSV_FIELDS = [
    "arm", "true_grammar", "impostor_id", "surface_seed",
    "frac", "rotation_deg", "degradation", "degradation_param", "wb_gain_bgr",
    "boundaries_spanned", "top_sheet", "top_aggregate",
    "verdict_001", "aggregate_001", "coverage_001", "renormalised_001",
    "verdict_002", "aggregate_002", "coverage_002", "renormalised_002",
    # 002 two-path ink match (SI-020) -- the max is the feature agreement.
    "ink002_agreement", "ink002_agr_absolute", "ink002_agr_relationship",
    "ink002_gain_b", "ink002_gain_g", "ink002_gain_r",
    "ink002_implied_b", "ink002_implied_g", "ink002_implied_r",
    "ink002_gain_in_bounds", "ink002_relationship_applicable",
    "ink002_n_inks", "ink002_n_clipped", "ink002_rank_corr",
]


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

@dataclass
class CrossConfig:
    """Declares the whole cross-grammar sweep. Defaults are the publishable run.

    ``frags_per_cell`` is PER surface seed, so a (frac, rotation) cell holds
    ``frags_per_cell * len(surface_seeds)`` fragments. With the defaults that is
    8 * 3 = 24 (>= 20 for stable per-cell statistics, as the task asks).
    """
    # 001 (band) surface geometry -- module 200 matches exp-001 so a full surface
    # reaches 'identified' (module 160 tops out at candidate).
    n_bands: int = 5
    module_px_001: int = 200
    # 002 (grid) surface geometry -- module 100, 10x10 cells -> 1000x1000, so both
    # families render to the same canvas size and the fragment fracs are comparable.
    module_px_002: int = 100
    cols_002: int = 10
    rows_002: int = 10
    density_002: float = 0.45

    surface_seeds: tuple = (0, 1, 2)
    frags_per_cell: int = 8

    fracs: tuple = (0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0)
    rotations: tuple = (0, 30, 90)

    # White-balance gain sweep (SI-020). Labelled by their perceptual sense; the
    # tuple is the (B, G, R) gain passed to degrade.white_balance -- warm boosts
    # red / cuts blue, cool the reverse (the task's (r,g,b) triples reversed to the
    # project's BGR convention). strong-warm pushes red to 1.3 and clips bright
    # reds -- the SI-020 clipping regime.
    wb_sets: tuple = (
        ("neutral", (1.0, 1.0, 1.0)),
        ("warm", (0.85, 1.0, 1.15)),
        ("cool", (1.15, 1.0, 0.85)),
        ("strong_warm", (0.7, 1.0, 1.3)),
    )
    wb_frac: float = 0.5

    # Impostor arm fracs (a subset spanning few-cells -> whole keeps it tractable).
    impostor_fracs: tuple = (0.1, 0.2, 0.35, 0.5, 0.75, 1.0)
    nine_ink_hue_search: tuple = tuple(range(40, 181, 5))

    base_seed: int = 20260706
    quick: bool = False


def quick_config() -> CrossConfig:
    """A tiny smoke config for CI / tests (seconds, not minutes)."""
    return CrossConfig(
        module_px_001=160,
        module_px_002=80,
        cols_002=8,
        rows_002=8,
        surface_seeds=(0,),
        frags_per_cell=2,
        fracs=(0.1, 0.5, 1.0),
        rotations=(0, 90),
        wb_sets=(
            ("neutral", (1.0, 1.0, 1.0)),
            ("strong_warm", (0.7, 1.0, 1.3)),
        ),
        impostor_fracs=(0.2, 0.5, 1.0),
        nine_ink_hue_search=(40, 60, 90),
        quick=True,
    )


# --------------------------------------------------------------------------- #
# Impostors
# --------------------------------------------------------------------------- #

def build_band_impostor(genuine_001: dict) -> dict:
    """The exp-001 canonical band impostor: ratio 2.0, duty 1/3, phase 1/3.

    An in-memory MODIFIED COPY of the 001 sheet (validation bypassed -- it is not
    a valid enrolled grammar), carrying 001's exact inks so colour never
    discriminates it and the false-positive margin rests on structure. This is the
    same look-alike exp-001 arm 3 used (SI-016); reproduced here scored against
    BOTH sheets so its 002 cross-score is on the record too.
    """
    s = copy.deepcopy(genuine_001)
    s["combination_rules"]["frequency_ratio"] = 2.0
    s["combination_rules"]["duty_cycle_light"] = 1.0 / 3.0
    s["combination_rules"]["phase_step"] = 1.0 / 3.0
    return {"id": "canonical_ratio2_duty33", "sheet": s, "family": "band", "n_bands": 5}


def _hex_to_lab(hexval: str) -> np.ndarray:
    return _score._hex_to_lab(hexval)


def _lab_to_hex(lab: np.ndarray) -> str:
    """CIE Lab (L 0..100, a,b ~[-127,127]) -> '#RRGGBB' via the cv2 float path."""
    arr = np.asarray(lab, dtype=np.float32).reshape(1, 1, 3)
    bgr = cv2.cvtColor(arr, cv2.COLOR_Lab2BGR)[0, 0]
    bgr = np.clip(bgr * 255.0, 0, 255).round().astype(int)
    b, g, r = int(bgr[0]), int(bgr[1]), int(bgr[2])
    return f"#{r:02X}{g:02X}{b:02X}"


def _rotate_hue_lab(hexval: str, theta_deg: float) -> str:
    """Rotate a colour's (a, b) Lab chroma by ``theta_deg``, keeping L.

    A Lab hue rotation preserves lightness and chroma but is NOT a per-channel
    diagonal gain in RGB -- which is exactly why it is the right impostor
    transform: it moves the inks far from 002's set in colour space AND cannot be
    undone by the relationship path's single diagonal white-balance gain (SI-020).
    So a nine-ink grid impostor tests structure-only leakage on BOTH ink paths.
    """
    lab = _hex_to_lab(hexval)
    th = np.deg2rad(theta_deg)
    a, b = lab[1], lab[2]
    a2 = a * np.cos(th) - b * np.sin(th)
    b2 = a * np.sin(th) + b * np.cos(th)
    return _lab_to_hex(np.array([lab[0], a2, b2], dtype=np.float32))


def _min_nearest_delta_e(impostor_hexes, master_hexes) -> float:
    """Min over impostor inks of the nearest master-ink delta-E76 (Lab)."""
    m_labs = [_hex_to_lab(h) for h in master_hexes]
    best = float("inf")
    for h in impostor_hexes:
        lab = _hex_to_lab(h)
        d = min(_score._delta_e76(lab, ml) for ml in m_labs)
        best = min(best, d)
    return best


def build_nine_ink_impostor(sheet_002: dict, hue_search) -> dict:
    """A grid composition rendered in NINE inks hue-rotated away from 002's set.

    Structurally identical to 002 (same grid, alphabet, overprint), but every ink
    is 002's master ink rotated in Lab hue by the ``theta`` in ``hue_search`` that
    MAXIMISES the min-nearest delta-E to 002's set (so colour clearly fails).
    Deterministic: the search is a fixed ordered scan, ties broken by first (which
    is smallest theta). Returns ``{id, family, inks, theta, min_delta_e}``.
    """
    master = [ink["value"].upper() for ink in sheet_002["colour_system"]["inks"]]
    best = None  # (min_delta_e, theta, inks)
    for theta in hue_search:
        inks = [_rotate_hue_lab(h, theta) for h in master]
        md = _min_nearest_delta_e(inks, master)
        if best is None or md > best[0]:
            best = (md, theta, inks)
    md, theta, inks = best
    return {"id": "nine_ink_grid", "family": "grid", "inks": inks,
            "theta_deg": float(theta), "min_delta_e": round(float(md), 3)}


# --------------------------------------------------------------------------- #
# Sweep primitives
# --------------------------------------------------------------------------- #

def _rng(config: CrossConfig, *ints) -> np.random.Generator:
    """Deterministic Generator keyed by the config base seed + integer path."""
    return np.random.default_rng(np.random.SeedSequence([config.base_seed, *ints]))


def _result_for(claim: dict, sheet_id: str):
    for r in claim["results"]:
        if r["sheet_id"] == sheet_id:
            return r
    return None


def _ink002_detail(claim: dict) -> dict:
    """Pull the 002 sheet's two-path ink-set feature detail from a claim (SI-020)."""
    r = _result_for(claim, SHEET_002_ID)
    if r is None:
        return {}
    for f in r["per_feature"]:
        if f["id"] == "ink_set":
            d = dict(f.get("detail", {}) or {})
            d["_agreement"] = f.get("agreement")
            return d
    return {}


def _sample(surface, frac, rotation, config, seed_path, *, module_px=None, n_bands=None):
    """Sample one fragment; returns (None, None) on an impossible rotated geometry.

    The sampler fix (SI-017 gap 2 / generator.fragments) means the ONLY skip left
    is the genuine geometric impossibility of fitting a large tilted window in the
    surface (ValueError), never a zero-size window.
    """
    rng = _rng(config, *seed_path)
    try:
        frag, info = sample_fragment(
            surface, frac=frac, rng=rng, rotation_deg=float(rotation),
            module_px=module_px, n_bands=n_bands,
        )
    except ValueError:
        return None, None
    return frag, info


def _row(arm, true_grammar, impostor_id, surface_seed, frac, rotation,
         degradation, param, wb_gain, info, claim) -> dict:
    """Assemble one CSV row from a fragment's ground truth + its recognise claim."""
    top = claim["results"][0]
    r001 = _result_for(claim, SHEET_001_ID)
    r002 = _result_for(claim, SHEET_002_ID)
    ink = _ink002_detail(claim)
    gain = ink.get("correction_gain_bgr") or [None, None, None]
    implied = ink.get("implied_applied_gain_bgr") or [None, None, None]
    clipping = ink.get("clipping") or {}
    span = getattr(info, "band_span", None) or (None, None)

    def agg(r, key):
        return r[key] if r is not None else None

    return {
        "arm": arm,
        "true_grammar": true_grammar,
        "impostor_id": impostor_id or "",
        "surface_seed": surface_seed,
        "frac": round(float(frac), 4),
        "rotation_deg": float(rotation),
        "degradation": degradation,
        "degradation_param": param,
        "wb_gain_bgr": ("|".join(f"{g:g}" for g in wb_gain) if wb_gain else ""),
        "boundaries_spanned": getattr(info, "band_boundaries_spanned", None),
        "top_sheet": top["sheet_id"],
        "top_aggregate": top["aggregate_confidence"],
        "verdict_001": agg(r001, "verdict"),
        "aggregate_001": agg(r001, "aggregate_confidence"),
        "coverage_001": agg(r001, "coverage"),
        "renormalised_001": agg(r001, "renormalised_score"),
        "verdict_002": agg(r002, "verdict"),
        "aggregate_002": agg(r002, "aggregate_confidence"),
        "coverage_002": agg(r002, "coverage"),
        "renormalised_002": agg(r002, "renormalised_score"),
        "ink002_agreement": ink.get("_agreement"),
        "ink002_agr_absolute": ink.get("agreement_absolute"),
        "ink002_agr_relationship": ink.get("agreement_relationship"),
        "ink002_gain_b": gain[0], "ink002_gain_g": gain[1], "ink002_gain_r": gain[2],
        "ink002_implied_b": implied[0], "ink002_implied_g": implied[1],
        "ink002_implied_r": implied[2],
        "ink002_gain_in_bounds": ink.get("gain_in_bounds"),
        "ink002_relationship_applicable": ink.get("relationship_applicable"),
        "ink002_n_inks": ink.get("n_inks"),
        "ink002_n_clipped": clipping.get("n_clipped_inks"),
        "ink002_rank_corr": ink.get("rank_correlation"),
    }


# --------------------------------------------------------------------------- #
# Main run
# --------------------------------------------------------------------------- #

def run_cross(config: CrossConfig, out_dir, timestamp: str, progress=True) -> dict:
    """Execute the whole cross-grammar battery; write all artifacts under out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sheet_001 = load_sheet(SHEET_001)
    sheet_002 = load_sheet(SHEET_002)

    rows: list[dict] = []
    skips = 0

    def emit(msg):
        if progress:
            print(msg, file=sys.stderr, flush=True)

    # Pre-render one surface per (family, seed). 002 varies its ink subset with the
    # seed (grid.select_ink_subset draws from the seed's rng), so the three 002
    # seeds are three different ink dialects, as the task asks.
    surf_001 = {s: cascade.render(sheet_001, n_bands=config.n_bands,
                                  module_px=config.module_px_001, seed=s,
                                  orientation_deg=0.0)
                for s in config.surface_seeds}
    surf_002 = {}
    ink_subsets_002 = {}
    for s in config.surface_seeds:
        surface, truth = grid.render_with_truth(
            sheet_002, cols=config.cols_002, rows=config.rows_002,
            module_px=config.module_px_002, seed=s, density=config.density_002)
        surf_002[s] = surface
        ink_subsets_002[s] = list(truth.ink_subset)

    # --- Arm A: 001 (band) surfaces, fraction x rotation, both sheets scored ----
    emit("arm 1/4: 001 band surfaces (fraction x rotation)")
    for ri, rotation in enumerate(config.rotations):
        for fi, frac in enumerate(config.fracs):
            for si, seed in enumerate(config.surface_seeds):
                surface = surf_001[seed]
                for k in range(config.frags_per_cell):
                    frag, info = _sample(surface, frac, rotation, config,
                                         (1, ri, fi, si, k),
                                         module_px=config.module_px_001,
                                         n_bands=config.n_bands)
                    if frag is None:
                        skips += 1
                        continue
                    claim = recognise(frag, str(GRAMMARS))
                    rows.append(_row("surface_001", SHEET_001_ID, None, seed,
                                     frac, rotation, "none", "", None, info, claim))

    # --- Arm B: 002 (grid) surfaces, fraction x rotation, both sheets scored ----
    emit("arm 2/4: 002 grid surfaces (fraction x rotation)")
    for ri, rotation in enumerate(config.rotations):
        for fi, frac in enumerate(config.fracs):
            for si, seed in enumerate(config.surface_seeds):
                surface = surf_002[seed]
                for k in range(config.frags_per_cell):
                    frag, info = _sample(surface, frac, rotation, config,
                                         (2, ri, fi, si, k))
                    if frag is None:
                        skips += 1
                        continue
                    claim = recognise(frag, str(GRAMMARS))
                    rows.append(_row("surface_002", SHEET_002_ID, None, seed,
                                     frac, rotation, "none", "", None, info, claim))

    # --- Arm C: white-balance sweep on 002 fragments at wb_frac -----------------
    emit("arm 3/4: white-balance sweep on 002 fragments (frac 0.5)")
    for wi, (label, gains) in enumerate(config.wb_sets):
        for si, seed in enumerate(config.surface_seeds):
            surface = surf_002[seed]
            for k in range(config.frags_per_cell):
                frag, info = _sample(surface, config.wb_frac, 0, config,
                                     (3, wi, si, k))
                if frag is None:
                    skips += 1
                    continue
                degraded = degrade.white_balance(frag, gains)
                claim = recognise(degraded, str(GRAMMARS))
                rows.append(_row("wb_002", SHEET_002_ID, None, seed,
                                 config.wb_frac, 0, "white_balance", label,
                                 gains, info, claim))

    # --- Arm D: mutual impostors, both scored against both sheets ---------------
    emit("arm 4/4: mutual impostors (canonical band + nine-ink grid)")
    band_imp = build_band_impostor(sheet_001)
    nine_imp = build_nine_ink_impostor(sheet_002, config.nine_ink_hue_search)

    band_surfaces = {
        s: cascade.render(band_imp["sheet"], n_bands=band_imp["n_bands"],
                          module_px=config.module_px_001, seed=s, orientation_deg=0.0)
        for s in config.surface_seeds}
    nine_surfaces = {
        s: grid.render(sheet_002, cols=config.cols_002, rows=config.rows_002,
                       module_px=config.module_px_002, seed=s,
                       density=config.density_002, ink_subset=nine_imp["inks"])
        for s in config.surface_seeds}

    for imp_idx, (imp, surfaces, mod, nb) in enumerate((
        (band_imp, band_surfaces, config.module_px_001, band_imp["n_bands"]),
        (nine_imp, nine_surfaces, None, None),
    )):
        for fi, frac in enumerate(config.impostor_fracs):
            for si, seed in enumerate(config.surface_seeds):
                surface = surfaces[seed]
                for k in range(config.frags_per_cell):
                    frag, info = _sample(surface, frac, 0, config,
                                         (4, imp_idx, fi, si, k),
                                         module_px=mod, n_bands=nb)
                    if frag is None:
                        skips += 1
                        continue
                    claim = recognise(frag, str(GRAMMARS))
                    rows.append(_row("impostor", "none", imp["id"], seed,
                                     frac, 0, "none", "", None, info, claim))

    emit(f"collected {len(rows)} fragment rows ({skips} skipped for geometry)")

    # --- raw_results.csv -----------------------------------------------------
    csv_path = out_dir / "raw_results.csv"
    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    # --- manifest.yaml -------------------------------------------------------
    sheets = list_sheets(GRAMMARS)
    manifest = {
        "experiment": "exp-002-cross-grammar",
        "timestamp": timestamp,
        "git_commit": _git_commit(),
        "command": "python -m battery.cross " + " ".join(sys.argv[1:]),
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
        "ink_subsets_002": {str(s): ink_subsets_002[s] for s in config.surface_seeds},
        "impostors": [
            {"id": band_imp["id"], "family": "band",
             "frequency_ratio": band_imp["sheet"]["combination_rules"]["frequency_ratio"],
             "duty_cycle_light": band_imp["sheet"]["combination_rules"]["duty_cycle_light"],
             "phase_step": band_imp["sheet"]["combination_rules"]["phase_step"],
             "note": "in-memory MODIFIED copy of the 001 sheet (validation bypassed); "
                     "carries 001's inks so colour cannot discriminate it (SI-016)"},
            {"id": nine_imp["id"], "family": "grid",
             "hue_rotation_deg": nine_imp["theta_deg"],
             "min_delta_e_to_002_set": nine_imp["min_delta_e"],
             "inks": nine_imp["inks"],
             "note": "002 structure (grid/alphabet/overprint) rendered in nine inks "
                     "hue-rotated far from 002's master set; structure is weight-0 in "
                     "002, so this should score ~0 -- the structure-only-leak test"},
        ],
        "rows": len(rows),
        "skipped_geometry": skips,
        "csv": "raw_results.csv",
    }
    manifest_path = out_dir / "manifest.yaml"
    with open(manifest_path, "w") as fh:
        yaml.safe_dump(manifest, fh, sort_keys=False)

    charts = _make_charts(rows, config, out_dir)

    return {
        "rows": len(rows),
        "skipped": skips,
        "csv": str(csv_path),
        "manifest": str(manifest_path),
        "charts": [str(c) for c in charts],
        "nine_ink_impostor": nine_imp,
    }


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), text=True
        ).strip()
    except Exception:
        return "unknown"


# --------------------------------------------------------------------------- #
# Charts (every number recomputed from the collected rows)
# --------------------------------------------------------------------------- #

def _mean_band(values):
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    if arr.size == 0:
        return None, None
    return float(arr.mean()), float(arr.std())


def _make_charts(rows, config: CrossConfig, out_dir) -> list:
    paths = []
    fracs = list(config.fracs)

    # 1. confidence-vs-frac per TRUE grammar (rotation 0): true-sheet vs
    #    cross-sheet curves on one plot -- the separation is the result.
    for arm, true_id, cross_id, tag in (
        ("surface_001", "aggregate_001", "aggregate_002", "001"),
        ("surface_002", "aggregate_002", "aggregate_001", "002"),
    ):
        sub = [r for r in rows if r["arm"] == arm and r["rotation_deg"] == 0.0]
        series = {}
        for key, label in ((true_id, f"vs {tag} (true sheet)"),
                           (cross_id, f"vs {'002' if tag == '001' else '001'} (cross sheet)")):
            means, bands = [], []
            for frac in fracs:
                vals = [r[key] for r in sub if abs(r["frac"] - frac) < 1e-6]
                m, b = _mean_band(vals)
                means.append(m)
                bands.append(b)
            series[label] = {"mean": means, "band": bands}
        paths.append(chart.line_chart(
            out_dir / f"curve_confidence_vs_frac_true_{tag}.png", series, fracs,
            xlabel="fragment fraction of surface area",
            ylabel="mean aggregate confidence",
            title=f"Cross-grammar confidence vs fraction (true grammar: {tag}, rot 0)",
            hlines=[(_score.IDENTIFIED_THRESHOLD, "identified 0.70"),
                    (_score.CANDIDATE_THRESHOLD, "candidate 0.40")]))

    # 2. confusion summary: verdict counts per (true-grammar x scored-sheet).
    verdicts = ("identified", "candidate", "not_recognised")
    groups, series = [], {v: [] for v in verdicts}
    for arm, tag in (("surface_001", "001"), ("surface_002", "002")):
        sub = [r for r in rows if r["arm"] == arm]
        for scored, vkey in (("001", "verdict_001"), ("002", "verdict_002")):
            groups.append(f"{tag}->{scored}")
            for v in verdicts:
                series[v].append(sum(1 for r in sub if r[vkey] == v))
    paths.append(chart.bar_chart(
        out_dir / "confusion_summary.png", groups, series,
        xlabel="true grammar -> scored sheet", ylabel="fragment count",
        title="Cross-grammar confusion (verdict counts, all fracs x rotations)"))

    # 3. white-balance robustness: absolute vs relationship agreement across the
    #    gain sweep, x = applied warmth (R gain / B gain).
    wb = [r for r in rows if r["arm"] == "wb_002"]
    labels_order = [lbl for lbl, _ in config.wb_sets]
    gain_by_label = {lbl: g for lbl, g in config.wb_sets}
    warmth = [gain_by_label[lbl][2] / gain_by_label[lbl][0] for lbl in labels_order]
    order = np.argsort(warmth)
    xs = [round(warmth[i], 4) for i in order]
    ordered_labels = [labels_order[i] for i in order]
    series_wb = {}
    for key, label in (("ink002_agr_absolute", "absolute path"),
                       ("ink002_agr_relationship", "relationship path"),
                       ("ink002_agreement", "feature agreement (max)")):
        means, bands = [], []
        for lbl in ordered_labels:
            vals = [r[key] for r in wb if r["degradation_param"] == lbl]
            m, b = _mean_band(vals)
            means.append(m)
            bands.append(b)
        series_wb[label] = {"mean": means, "band": bands}
    paths.append(chart.line_chart(
        out_dir / "wb_robustness.png", series_wb, xs,
        xlabel="applied warmth (R gain / B gain)",
        ylabel="mean 002 ink agreement",
        title="White-balance robustness: absolute vs relationship path (002, frac 0.5)",
        hlines=[(_score.CANDIDATE_THRESHOLD, "candidate 0.40")]))

    return paths


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the cross-grammar recognition experiment (exp-002).")
    parser.add_argument("--out", default="experiments/exp-002-cross-grammar",
                        help="output directory for artifacts")
    parser.add_argument("--quick", action="store_true",
                        help="tiny smoke config (CI/tests)")
    args = parser.parse_args(argv)

    config = quick_config() if args.quick else CrossConfig()
    timestamp = datetime.now(timezone.utc).isoformat()
    summary = run_cross(config, args.out, timestamp)
    print(f"wrote {summary['rows']} rows to {summary['csv']}")
    print(f"manifest: {summary['manifest']}")
    for c in summary["charts"]:
        print(f"chart: {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
