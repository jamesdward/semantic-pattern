"""Tests for the grid-composition generator (grammar iso-002 / Studio.Build).

Project rule (README, tests row): every generator is validated against synthetic
ground truth BY MEASUREMENT. These tests render grid compositions from the
iso-002 sheet and measure the audit-002 quantities back out of the pixels --
the module grid (edge-projection autocorrelation), the stripe rhythm (run
lengths), the inscribed-circle diameter, and the EXACT multiply overprint
(colour arithmetic) -- then check the strong whole-image ink invariant,
determinism, and that the GroundTruth records what was placed.
"""

import numpy as np
import pytest

from pathlib import Path

from sheets import load_sheet
from generator import grid

REPO_ROOT = Path(__file__).resolve().parent.parent
SHEET_PATH = REPO_ROOT / "grammars" / "iso-002.yaml"

COLS = 12
ROWS = 8
MODULE = 72


@pytest.fixture(scope="module")
def sheet():
    return load_sheet(SHEET_PATH)


@pytest.fixture(scope="module")
def render0(sheet):
    """The director's eyeball sample: 12x8 cells, module 72, seed 0."""
    return grid.render_with_truth(sheet, cols=COLS, rows=ROWS, module_px=MODULE, seed=0)


# --- measurement helpers ----------------------------------------------------


def _run_lengths(mask_1d):
    """Lengths of consecutive True and consecutive False runs down a bool vector."""
    runs = []
    if mask_1d.size == 0:
        return runs
    cur = bool(mask_1d[0])
    length = 1
    for v in mask_1d[1:]:
        if bool(v) == cur:
            length += 1
        else:
            runs.append((cur, length))
            cur = bool(v)
            length = 1
    runs.append((cur, length))
    return runs


def _edge_projection_autocorr(surface):
    """1D autocorrelation of the vertical-edge projection (lag 0..W-1).

    Vertical edges (|d/dx| of the grey surface) summed over rows give a profile
    that spikes at x = k*module; its autocorrelation peaks at lag = module and
    its multiples (audit s2: edge-projection autocorrelation finds the lattice).
    """
    grey = surface.astype(np.float64).sum(axis=2)
    dx = np.abs(np.diff(grey, axis=1)).sum(axis=0)
    dx = dx - dx.mean()
    full = np.correlate(dx, dx, mode="full")
    return full[full.size // 2 :]  # non-negative lags


# --- grid detectable (audit s2) ---------------------------------------------


def test_grid_module_detected_by_autocorrelation(render0):
    surface, _ = render0
    ac = _edge_projection_autocorr(surface)
    # Search lags around the module; the dominant short-range period is M.
    lags = np.arange(MODULE // 2, 3 * MODULE)
    best = lags[np.argmax(ac[lags])]
    assert abs(best - MODULE) <= 2
    # The lattice recurs at integer multiples: 2M is also a local autocorr peak.
    win = 3
    assert ac[2 * MODULE] >= ac[2 * MODULE - MODULE // 2]
    assert ac[2 * MODULE] == ac[2 * MODULE - win : 2 * MODULE + win + 1].max()


# --- stripe rhythm: bar M/2, pitch M, duty 0.5 (audit s2) -------------------


def test_stripe_block_mask_rhythm():
    """Measure run lengths in a stripe block: bars M/2, pitch M, duty 0.5."""
    mask = grid.stripe_block_mask(ROWS, COLS, MODULE, r=1, c=1, w=2, h=3)
    col = mask[:, 1 * MODULE + MODULE // 2]  # a column through the block
    # Interior runs of the block (drop the leading/trailing edge runs).
    runs = _run_lengths(col[1 * MODULE : (1 + 3) * MODULE])
    bar_runs = [n for on, n in runs if on]
    gap_runs = [n for on, n in runs if not on]
    assert all(abs(n - MODULE // 2) <= 1 for n in bar_runs)
    assert all(abs(n - MODULE // 2) <= 1 for n in gap_runs)
    # Duty over the whole block column is 0.5.
    duty = col[1 * MODULE : (1 + 3) * MODULE].mean()
    assert abs(duty - 0.5) < 0.03


def _footprint(shape, r0, c0, h, w):
    foot = np.zeros(shape, dtype=bool)
    foot[r0 * MODULE : (r0 + h) * MODULE, c0 * MODULE : (c0 + w) * MODULE] = True
    return foot


def _is_clean(gt, mask, foot):
    """True if, within ``foot``, the only ink printed is this primitive's own
    mask: depth is 1 exactly where the mask inks and 0 elsewhere (no overprint,
    and no other primitive intruded on the gaps)."""
    return np.array_equal(gt.depth[foot], mask[foot].astype(gt.depth.dtype))


def test_stripe_rhythm_in_render(sheet):
    """Find a stripe block that printed un-overprinted and measure its rhythm.

    Uses a low density so clean (unoverlapped) blocks occur; scans seeds
    deterministically until one is found (guaranteed at these params).
    """
    for seed in range(60):
        surface, gt = grid.render_with_truth(
            sheet, cols=COLS, rows=ROWS, module_px=MODULE, seed=seed, density=0.15
        )
        for rec in gt.primitives:
            if rec.type != "stripe_bar":
                continue
            w, h = rec.params["w"], rec.params["h"]
            r0, c0 = min(c[0] for c in rec.cells), min(c[1] for c in rec.cells)
            mask = grid.stripe_block_mask(ROWS, COLS, MODULE, r0, c0, w, h)
            foot = _footprint(gt.depth.shape, r0, c0, h, w)
            if not _is_clean(gt, mask, foot):
                continue
            x = c0 * MODULE + MODULE // 2
            block = slice(r0 * MODULE, (r0 + h) * MODULE)
            # Inked where the surface is not white ground; must match the mask.
            surf_inked = (surface[block, x] != 255).any(axis=1)
            assert np.array_equal(surf_inked, mask[block, x])
            bars = [n for on, n in _run_lengths(surf_inked) if on]
            gaps = [n for on, n in _run_lengths(surf_inked)[1:-1] if not on]
            assert all(abs(n - MODULE // 2) <= 1 for n in bars)
            assert all(abs(n - MODULE // 2) <= 1 for n in gaps)  # pitch M
            return
    pytest.fail("no un-overprinted stripe block found across seed sweep")


# --- inscribed circle: Ø/M in {1, 2} (audit s2) -----------------------------


def _mask_max_width(mask):
    """Widest run of True in any row (the circle's measured diameter in px)."""
    widths = mask.sum(axis=1)
    return int(widths.max())


def test_circle_mask_diameter_ratios():
    m1 = grid.circle_mask(ROWS, COLS, MODULE, r=2, c=2, scale=1)
    m2 = grid.circle_mask(ROWS, COLS, MODULE, r=2, c=2, scale=2)
    d1 = _mask_max_width(m1)
    d2 = _mask_max_width(m2)
    # radius/M = diameter/(2M) in {0.5, 1.0}.
    assert abs(d1 / (2 * MODULE) - 0.5) < 0.03
    assert abs(d2 / (2 * MODULE) - 1.0) < 0.03


def test_circle_diameter_in_render(sheet):
    """Measure a known Ø1M circle record's diameter from its clean footprint."""
    for seed in range(60):
        surface, gt = grid.render_with_truth(
            sheet, cols=COLS, rows=ROWS, module_px=MODULE, seed=seed, density=0.15
        )
        for rec in gt.primitives:
            if rec.type != "inscribed_circle" or rec.params.get("scale") != 1:
                continue
            r0, c0 = rec.cells[0]
            mask = grid.circle_mask(ROWS, COLS, MODULE, r0, c0, 1)
            foot = _footprint(gt.depth.shape, r0, c0, 1, 1)
            if not _is_clean(gt, mask, foot):
                continue
            surf_inked = (surface != 255).any(axis=2) & foot
            assert abs(_mask_max_width(surf_inked) / (2 * MODULE) - 0.5) < 0.03
            return
    pytest.fail("no un-overprinted Ø1M circle found across seed sweep")


# --- overprint EXACT: overlap == multiply(parents) (audit s2) ---------------


def test_overprint_pixels_are_exact_multiply(render0):
    """Every recorded overlap pair reads back the exact multiply of its parents."""
    surface, gt = render0
    assert len(gt.overlaps) > 0
    for o in gt.overlaps:
        a, b = (grid._hex_to_bgr(h) for h in o.inks)
        predicted = tuple(int(v) for v in grid.multiply(a, b))
        assert o.product == predicted
        # Sample every depth-2 pixel carrying exactly this master-set pair.
        pair_pixels = _overlap_pixels(gt, o)
        assert pair_pixels.shape[0] > 0
        vals = surface[pair_pixels[:, 0], pair_pixels[:, 1]]
        assert np.all(vals == np.array(predicted, dtype=np.uint8))


def _overlap_pixels(gt, overlap):
    """(y, x) coords of depth-2 pixels whose ink pair matches ``overlap.inks``."""
    two = gt.depth == 2
    idx = {h: i for i, h in enumerate(gt.ink_subset)}
    a, b = idx[overlap.inks[0]], idx[overlap.inks[1]]
    lo, hi = np.minimum(gt.label_first, gt.label_second), np.maximum(
        gt.label_first, gt.label_second
    )
    sel = two & (lo == min(a, b)) & (hi == max(a, b))
    ys, xs = np.nonzero(sel)
    return np.stack([ys, xs], axis=1)


# --- whole-image ink invariant (strong; audit s2/s4) ------------------------


def test_output_colours_subset_of_inks_and_pairwise_products(render0):
    """Unique output colours ⊆ {white} ∪ ink subset ∪ pairwise multiply products."""
    surface, gt = render0
    inks = [grid._hex_to_bgr(h) for h in gt.ink_subset]
    allowed = {(255, 255, 255)}
    for c in inks:
        allowed.add(tuple(int(v) for v in c))
    for i in range(len(inks)):
        for j in range(i, len(inks)):
            allowed.add(tuple(int(v) for v in grid.multiply(inks[i], inks[j])))
    unique = set(map(tuple, surface.reshape(-1, 3).tolist()))
    assert unique <= allowed
    # And overprint never went deeper than a pairwise product (depth cap).
    assert int(gt.depth.max()) <= grid.MAX_DEPTH


# --- ink subset selection (audit s3) ----------------------------------------


def test_ink_subset_is_4_to_6_master_inks(sheet, render0):
    _, gt = render0
    master = {h for h, _ in grid.master_inks(sheet)}
    assert 4 <= len(gt.ink_subset) <= 6
    assert set(gt.ink_subset) <= master
    assert len(set(gt.ink_subset)) == len(gt.ink_subset)  # distinct


def test_ink_subset_reproducible_from_seed(sheet):
    a = grid.select_ink_subset(sheet, np.random.default_rng(0))
    b = grid.select_ink_subset(sheet, np.random.default_rng(0))
    assert [h for h, _ in a] == [h for h, _ in b]
    # And matches the subset render draws from the same seed (leading rng draws).
    _, gt = grid.render_with_truth(sheet, cols=COLS, rows=ROWS, module_px=MODULE, seed=0)
    assert gt.ink_subset == [h for h, _ in a]


def test_explicit_ink_subset_used(sheet):
    subset = ["#FAFF54", "#D52EB2", "#75FB63", "#C5111D"]
    _, gt = grid.render_with_truth(
        sheet, cols=COLS, rows=ROWS, module_px=MODULE, seed=1, ink_subset=subset
    )
    assert gt.ink_subset == subset


# --- ground truth records what was placed -----------------------------------


def test_primitive_counts_match_records(render0):
    _, gt = render0
    counted = {t: 0 for t in grid.PRIMITIVE_TYPES}
    for rec in gt.primitives:
        counted[rec.type] += 1
    assert gt.primitive_counts == counted
    assert sum(gt.primitive_counts.values()) == len(gt.primitives)
    # density -> instance count: round(density * cols * rows).
    assert len(gt.primitives) == round(0.45 * COLS * ROWS)


def test_ground_truth_to_dict_is_json_friendly(render0):
    _, gt = render0
    d = gt.to_dict()
    assert d["module_px"] == MODULE
    assert d["cols"] == COLS and d["rows"] == ROWS
    assert set(d["primitive_counts"]) == set(grid.PRIMITIVE_TYPES)
    assert len(d["primitives"]) == len(gt.primitives)
    for o in d["overlaps"]:
        assert len(o["inks"]) == 2 and len(o["product"]) == 3


# --- determinism (README principle 4) ---------------------------------------


def test_determinism_array(sheet):
    a = grid.render(sheet, cols=COLS, rows=ROWS, module_px=MODULE, seed=7)
    b = grid.render(sheet, cols=COLS, rows=ROWS, module_px=MODULE, seed=7)
    assert np.array_equal(a, b)


def test_determinism_png_byte_identical(sheet, tmp_path):
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    grid.render_png(sheet, p1, cols=COLS, rows=ROWS, module_px=MODULE, seed=3)
    grid.render_png(sheet, p2, cols=COLS, rows=ROWS, module_px=MODULE, seed=3)
    assert p1.read_bytes() == p2.read_bytes()


def test_render_shape_and_dtype(render0):
    surface, _ = render0
    assert surface.shape == (ROWS * MODULE, COLS * MODULE, 3)
    assert surface.dtype == np.uint8
