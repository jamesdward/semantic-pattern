"""Grid-composition surface generator (grammar iso-002 / Studio.Build).

Renders a *free figurative composition* on a square module grid from a loaded
grammar sheet (``grammars/iso-002.yaml``). Everything structural is read FROM the
sheet -- the grid (``structure.grid``), the five primitives
(``primitive_alphabet``), the master inks (``colour_system.inks``) and the
layering rule (``colour_system.layering`` / ``combination_rules.overprint_rule``).
Nothing about the genome is hardcoded here; only the *composition/density* model
is local, and that is a dialect freedom the audit leaves open (audit s3, and
SPEC-ISSUES SI-018).

The genome (Pattern Grammar Audit 002, section 2):

  * GRID. One dominant square lattice, module M, with structure at 1M and 2M
    cell scales (sheet ``structure.grid.scales``). Every primitive is placed on
    the grid and every primitive edge lands on a cell (or half-cell) boundary.
  * FIVE PRIMITIVES, all module-relative (audit s2 primitive table / sheet
    ``primitive_alphabet``):
      - filled_cell        1x1 filled square cell.
      - inscribed_circle   circle inscribed in a 1x1 (Ø1M) or 2x2 (Ø2M) block.
      - stripe_bar         a w x h-cell rectangle of horizontal stripes: bar
                           height M/2, pitch 1M, duty 0.5.
      - staircase_diagonal a run of 1M x 1M cells stepping (+1,+1) or (+1,-1)
                           -- an exact 45deg staircase in cells.
      - rounded_cap        a stadium bar (1 x n cells, M/2-radius end caps) or a
                           quarter-round corner cell (fillet radius M/2).
  * WHITE GROUND. The composite starts white; the ink system needs it, exactly
    as overprint needs paper (audit s2).
  * MULTIPLY OVERPRINT, EXACT. Where two inks cross, the overlap colour is the
    channel-wise multiply of the parents, c = (c1*c2 + 127)//255 (audit s2
    verified this to the integer -- see MULTIPLY_BIAS below). The composite is
    built by multiplying flat ink layers, NOT by alpha blending, so every output
    colour is derivable from its parents.

Rendering choices (documented per task, README principle 4):

  * COLOUR ORDER: BGR uint8, the project-wide convention (cv2.imread/imwrite
    round-trip). ``render`` returns (H, W, 3) BGR with H = rows*module_px,
    W = cols*module_px.
  * NO ANTI-ALIASING. Every primitive is a HARD, strictly two-valued mask built
    from integer/numpy comparisons (numpy disc/segment distance tests, not cv2
    with a smoothing lineType). AA is deliberately absent for two reasons the
    audit forces: the flat spot-ink aesthetic has two-valued edges, and AA would
    blend intermediate colours at edges that are NOT the multiply of any ink
    pair -- corrupting the arithmetic the audit verified to the integer. Hard
    masks at 1x keep the arithmetic exactly checkable everywhere.
  * OVERPRINT DEPTH CAPPED AT 2. Multiply is commutative and (with white as an
    exact identity: (255*c+127)//255 == c) associative up to per-step rounding,
    so triple overprints would be *near* the triple product but could round off
    by a unit and, worse, would populate the output with triple-product colours.
    To keep the strong whole-image invariant checkable (every output colour is
    white, a lone ink, or a *pairwise* ink product), a third ink does not print
    where two already overlap: printing is clipped to pixels with current depth
    < 2. This is the smallest choice that keeps the exact-overprint test
    non-combinatorial (task constraint, SPEC-ISSUES SI-019); triple overlaps are
    rare at the default density and simply keep their 2-deep colour.

Determinism (README principle 4): all randomness flows through a single
``np.random.default_rng(seed)``; same sheet + params + seed produce a
byte-identical array and PNG.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Multiply-overprint rounding bias. The audit measured overlaps against
# c1*c2/255 and verified the *rounded* integer exactly (audit s2 table): e.g.
# green x yellow -> (115, 251, 33). Rounding half-up via integer arithmetic,
# (c1*c2 + 127)//255, reproduces every verified product and makes white an exact
# multiply identity ((255*c + 127)//255 == c). Half-up (not numpy banker's
# rounding) is the choice, fixed here once so the overprint is byte-deterministic.
MULTIPLY_BIAS = 127

# The five primitive type ids (sheet primitive_alphabet ids, audit s2). Ordered;
# the composition selects uniformly among them by index so the choice is seeded.
PRIMITIVE_TYPES = (
    "filled_cell",
    "inscribed_circle",
    "stripe_bar",
    "staircase_diagonal",
    "rounded_cap",
)

# Overprint depth cap (see module docstring): a pixel carries at most this many
# inks, so every output colour is white, a lone ink, or a pairwise product.
MAX_DEPTH = 2


# --- colour helpers ---------------------------------------------------------


def _hex_to_bgr(value: str) -> np.ndarray:
    """'#RRGGBB' -> np.array([B, G, R], uint8). Colour space per sheet (sRGB-hex)."""
    value = value.lstrip("#")
    r = int(value[0:2], 16)
    g = int(value[2:4], 16)
    b = int(value[4:6], 16)
    return np.array([b, g, r], dtype=np.uint8)


def multiply(a, b):
    """Channel-wise multiply overprint, c = (c1*c2 + MULTIPLY_BIAS)//255.

    The exact print-separation rule the audit verified to the integer (audit s2).
    Accepts scalars or numpy arrays (broadcasting); returns the same shape. Inputs
    are taken as integers in 0..255, output is integer in 0..255.
    """
    a = np.asarray(a, dtype=np.int64)
    b = np.asarray(b, dtype=np.int64)
    return (a * b + MULTIPLY_BIAS) // 255


def master_inks(sheet: dict) -> list[tuple[str, np.ndarray]]:
    """Return the master ink set as [(hex, bgr), ...] in sheet order (audit s4)."""
    inks = []
    for ink in sheet["colour_system"]["inks"]:
        inks.append((ink["value"].upper(), _hex_to_bgr(ink["value"])))
    return inks


def select_ink_subset(sheet: dict, rng: np.random.Generator) -> list[tuple[str, np.ndarray]]:
    """Seeded choice of 4-6 inks from the master set (audit s3: each variant
    speaks a subset of the master inks).

    Draws first the subset size k in {4, 5, 6} then k distinct inks without
    replacement, both from ``rng`` -- so the subset is reproducible from the seed
    that made ``rng`` and forms the leading draws of ``render``'s rng stream.
    """
    inks = master_inks(sheet)
    k = int(rng.integers(4, 7))  # 4, 5 or 6 inks (audit s3)
    k = min(k, len(inks))
    idx = rng.choice(len(inks), size=k, replace=False)
    return [inks[i] for i in idx]


# --- primitive masks (audit s2 alphabet; all dimensions module-relative) ----
#
# Every builder returns a strictly two-valued bool array of shape (rows*M,
# cols*M). Masks are built from integer/numpy comparisons only -- no anti-alias,
# so edges are hard and the multiply arithmetic stays exact (module docstring).


def _grid_xy(rows: int, cols: int, module_px: int):
    """Return (X, Y) pixel-centre coordinate grids (float) for the surface."""
    h, w = rows * module_px, cols * module_px
    ys = np.arange(h, dtype=np.float64) + 0.5
    xs = np.arange(w, dtype=np.float64) + 0.5
    return np.meshgrid(xs, ys)  # both (h, w)


def filled_cell_mask(rows, cols, module_px, r, c) -> np.ndarray:
    """1x1 filled square cell at grid position (r, c) (audit s2 filled cell)."""
    m = module_px
    mask = np.zeros((rows * m, cols * m), dtype=bool)
    mask[r * m : (r + 1) * m, c * m : (c + 1) * m] = True
    return mask


def circle_mask(rows, cols, module_px, r, c, scale) -> np.ndarray:
    """Inscribed circle at (r, c). scale 1 -> Ø1M in a 1x1 cell; scale 2 -> Ø2M
    in the 2x2 block whose top-left cell is (r, c) (audit s2 inscribed circle).

    Radius is scale*M/2 and the centre is the block centre, so the circle is
    exactly inscribed (touches the block edges).
    """
    m = module_px
    radius = scale * m / 2.0
    cx = (c + scale / 2.0) * m
    cy = (r + scale / 2.0) * m
    X, Y = _grid_xy(rows, cols, module_px)
    return (X - cx) ** 2 + (Y - cy) ** 2 <= radius ** 2


def stripe_block_mask(rows, cols, module_px, r, c, w, h) -> np.ndarray:
    """A w x h-cell rectangle of horizontal stripes at (r, c) (audit s2 stripe).

    Bar height M/2, pitch 1M, duty 0.5: within the block, a pixel is ink where
    its row offset modulo M is below M/2. The stripe phase is pinned to the block
    top so the rhythm is measurable from the block alone.
    """
    m = module_px
    mask = np.zeros((rows * m, cols * m), dtype=bool)
    y0, y1 = r * m, (r + h) * m
    x0, x1 = c * m, (c + w) * m
    yy = np.arange(y0, y1)
    stripe_rows = ((yy - y0) % m) < (m // 2)  # bar M/2, pitch M, duty 0.5
    block = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    block[stripe_rows, :] = True
    mask[y0:y1, x0:x1] = block
    return mask


def staircase_mask(rows, cols, module_px, r, c, n, direction) -> np.ndarray:
    """A run of n filled 1x1 cells stepping (+1, direction) per step (audit s2).

    ``direction`` is +1 (down-right) or -1 (up-right); each step advances one
    cell right and one cell vertically -- an exact 45deg staircase in cells.
    Out-of-grid cells are dropped (the visible run is clipped to the surface).
    """
    m = module_px
    mask = np.zeros((rows * m, cols * m), dtype=bool)
    for i in range(n):
        rr, cc = r + i * direction, c + i
        if 0 <= rr < rows and 0 <= cc < cols:
            mask[rr * m : (rr + 1) * m, cc * m : (cc + 1) * m] = True
    return mask


def stadium_mask(rows, cols, module_px, r, c, n, vertical=False) -> np.ndarray:
    """A stadium (rounded) bar spanning n cells with M/2-radius end caps (audit s2).

    Horizontal: a 1-cell-tall bar over cells (r, c..c+n-1); the cap centres sit
    at the first/last cell centres and the radius is M/2, so the bar is exactly
    M tall and n*M long with semicircular ends. ``vertical`` transposes the run.
    """
    m = module_px
    radius = m / 2.0
    X, Y = _grid_xy(rows, cols, module_px)
    if not vertical:
        yc = (r + 0.5) * m
        x0 = (c + 0.5) * m
        x1 = (c + n - 0.5) * m
        # Distance to the horizontal centre segment [x0, x1] at height yc.
        px = np.clip(X, x0, x1)
        return (X - px) ** 2 + (Y - yc) ** 2 <= radius ** 2
    xc = (c + 0.5) * m
    y0 = (r + 0.5) * m
    y1 = (r + n - 0.5) * m
    py = np.clip(Y, y0, y1)
    return (X - xc) ** 2 + (Y - py) ** 2 <= radius ** 2


def quarter_round_mask(rows, cols, module_px, r, c, corner) -> np.ndarray:
    """A 1x1 cell with one corner rounded by a fillet of radius M/2 (audit s2).

    ``corner`` in {0,1,2,3} = top-left, top-right, bottom-right, bottom-left. The
    cell is filled except for the region of the chosen corner lying outside a
    quarter circle of radius M/2 centred at the cell centre-offset inset point.
    """
    m = module_px
    radius = m / 2.0
    cell = filled_cell_mask(rows, cols, module_px, r, c)
    X, Y = _grid_xy(rows, cols, module_px)
    # Inset centre for the fillet on the chosen corner (M/2 in from both edges).
    corners = {
        0: (c * m + radius, r * m + radius),
        1: ((c + 1) * m - radius, r * m + radius),
        2: ((c + 1) * m - radius, (r + 1) * m - radius),
        3: (c * m + radius, (r + 1) * m - radius),
    }
    ix, iy = corners[corner]
    # Which cell pixels are "beyond" the inset centre toward the chosen corner.
    beyond_x = X < ix if corner in (0, 3) else X > ix
    beyond_y = Y < iy if corner in (0, 1) else Y > iy
    outside_fillet = (X - ix) ** 2 + (Y - iy) ** 2 > radius ** 2
    cut = cell & beyond_x & beyond_y & outside_fillet
    return cell & ~cut


# --- ground truth -----------------------------------------------------------


@dataclass
class PrimitiveRecord:
    """Ground truth for one placed primitive instance."""

    type: str                       # one of PRIMITIVE_TYPES (sheet primitive id)
    cells: list                     # [[r, c], ...] grid cells the primitive occupies
    ink: str                        # ink hex it is drawn in
    params: dict                    # per-type params (scale / n / direction / ...)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "cells": [list(cell) for cell in self.cells],
            "ink": self.ink,
            "params": dict(self.params),
        }


@dataclass
class OverlapRecord:
    """Ground truth for one present two-ink overlap (audit s2 overprint)."""

    inks: tuple                     # (hex_a, hex_b), sorted by master-set index
    product: tuple                  # multiply(a, b) as (B, G, R), the exact overlap colour
    pixel_count: int                # pixels carrying exactly this pair (depth 2)
    sample_xy: tuple                # one (x, y) pixel guaranteed inside the region

    def to_dict(self) -> dict:
        return {
            "inks": list(self.inks),
            "product": list(self.product),
            "pixel_count": int(self.pixel_count),
            "sample_xy": list(self.sample_xy),
        }


@dataclass
class GroundTruth:
    """Full ground truth returned alongside a rendered grid composition.

    Serialisable fields describe the composition for manifests; the label/depth
    arrays (excluded from ``to_dict``) let tests verify overprint pixel-exactly.
    """

    module_px: int
    cols: int
    rows: int
    ink_subset: list                # ink hexes used (audit s3 subset)
    primitives: list                # list[PrimitiveRecord]
    primitive_counts: dict          # {type: count} -- primitive_frequency_mix (sheet SI/audit s6.2)
    overlaps: list                  # list[OverlapRecord]
    # Per-pixel truth for tests (not serialised). depth: inks applied (0..2);
    # label_first/label_second: master-set ink indices at depths 1 and 2, else -1.
    depth: np.ndarray = field(default=None, repr=False)
    label_first: np.ndarray = field(default=None, repr=False)
    label_second: np.ndarray = field(default=None, repr=False)

    def to_dict(self) -> dict:
        """Plain-dict form for the battery's JSON manifests (arrays excluded)."""
        return {
            "module_px": int(self.module_px),
            "cols": int(self.cols),
            "rows": int(self.rows),
            "ink_subset": list(self.ink_subset),
            "primitives": [p.to_dict() for p in self.primitives],
            "primitive_counts": dict(self.primitive_counts),
            "overlaps": [o.to_dict() for o in self.overlaps],
        }


# --- placement --------------------------------------------------------------


def _place_primitive(rng, rows, cols, module_px, type_):
    """Choose seeded params and build the mask + occupied cells for one instance.

    Returns (mask, cells, params). All geometry is grid-aligned and clipped to
    the surface. Sizes/orientations/positions are drawn from ``rng`` (audit s3:
    composition is free within the fixed alphabet).
    """
    m = module_px
    if type_ == "filled_cell":
        r = int(rng.integers(0, rows))
        c = int(rng.integers(0, cols))
        mask = filled_cell_mask(rows, cols, m, r, c)
        return mask, [[r, c]], {}

    if type_ == "inscribed_circle":
        scale = 2 if (rows >= 2 and cols >= 2 and rng.random() < 0.35) else 1
        r = int(rng.integers(0, rows - scale + 1))
        c = int(rng.integers(0, cols - scale + 1))
        mask = circle_mask(rows, cols, m, r, c, scale)
        cells = [[r + dr, c + dc] for dr in range(scale) for dc in range(scale)]
        return mask, cells, {"scale": scale}

    if type_ == "stripe_bar":
        w = int(rng.integers(1, min(3, cols) + 1))
        h = int(rng.integers(1, min(3, rows) + 1))
        r = int(rng.integers(0, rows - h + 1))
        c = int(rng.integers(0, cols - w + 1))
        mask = stripe_block_mask(rows, cols, m, r, c, w, h)
        cells = [[r + dr, c + dc] for dr in range(h) for dc in range(w)]
        return mask, cells, {"w": w, "h": h}

    if type_ == "staircase_diagonal":
        n = int(rng.integers(2, min(5, cols) + 1))
        direction = 1 if rng.random() < 0.5 else -1
        c = int(rng.integers(0, max(1, cols - n + 1)))
        if direction == 1:
            r = int(rng.integers(0, max(1, rows - n + 1)))
        else:
            r = int(rng.integers(n - 1, rows)) if rows >= n else rows - 1
        mask = staircase_mask(rows, cols, m, r, c, n, direction)
        cells = [
            [r + i * direction, c + i]
            for i in range(n)
            if 0 <= r + i * direction < rows and 0 <= c + i < cols
        ]
        return mask, cells, {"n": n, "direction": direction}

    # rounded_cap: stadium bar (n >= 2) or quarter-round corner cell (n == 1).
    vertical = rng.random() < 0.5
    axis = rows if vertical else cols
    n = int(rng.integers(1, min(4, axis) + 1))
    if n == 1:
        corner = int(rng.integers(0, 4))
        r = int(rng.integers(0, rows))
        c = int(rng.integers(0, cols))
        mask = quarter_round_mask(rows, cols, m, r, c, corner)
        return mask, [[r, c]], {"shape": "quarter_round", "corner": corner}
    if vertical:
        c = int(rng.integers(0, cols))
        r = int(rng.integers(0, rows - n + 1))
        cells = [[r + i, c] for i in range(n)]
    else:
        r = int(rng.integers(0, rows))
        c = int(rng.integers(0, cols - n + 1))
        cells = [[r, c + i] for i in range(n)]
    mask = stadium_mask(rows, cols, m, r, c, n, vertical=vertical)
    return mask, cells, {"shape": "stadium", "n": n, "vertical": bool(vertical)}


def _summarise_overlaps(depth, label_first, label_second, ink_hexes, ink_bgrs, master_index):
    """Build OverlapRecords from the depth-2 pixels (audit s2 overprint)."""
    overlaps = []
    two = depth == 2
    if not two.any():
        return overlaps
    a = label_first[two]
    b = label_second[two]
    # Order each pair by master-set index so (a,b) and (b,a) collapse; multiply is
    # commutative so the product is unaffected.
    lo = np.minimum(master_index[a], master_index[b])
    hi = np.maximum(master_index[a], master_index[b])
    ys, xs = np.nonzero(two)
    seen = {}
    for k in range(lo.size):
        key = (int(lo[k]), int(hi[k]))
        rec = seen.get(key)
        if rec is None:
            seen[key] = [1, (int(xs[k]), int(ys[k]))]
        else:
            rec[0] += 1
    # Map master index back to subset position for colours.
    master_to_subset = {int(master_index[i]): i for i in range(len(ink_hexes))}
    for (mi_lo, mi_hi), (count, sample) in sorted(seen.items()):
        i, j = master_to_subset[mi_lo], master_to_subset[mi_hi]
        product = multiply(ink_bgrs[i], ink_bgrs[j])
        overlaps.append(
            OverlapRecord(
                inks=(ink_hexes[i], ink_hexes[j]),
                product=tuple(int(v) for v in product),
                pixel_count=count,
                sample_xy=sample,
            )
        )
    return overlaps


def render_with_truth(
    sheet: dict,
    *,
    cols: int,
    rows: int,
    module_px: int,
    seed: int,
    ink_subset=None,
    density: float = 0.45,
    types=None,
):
    """Render a grid composition and its GroundTruth; return (surface, truth).

    Parameters
      sheet       a sheet dict as returned by sheets.load_sheet (iso-002).
      cols, rows  grid size in cells; surface is (rows*module_px, cols*module_px).
      module_px   the module M in pixels (audit s2 grid).
      seed        instance seed; all randomness flows through default_rng(seed).
      ink_subset  optional explicit list of ink hexes; default: a seeded 4-6 ink
                  subset of the master set (audit s3).
      density     composition density (audit s3 dialect freedom, SI-018): the
                  number of placed primitive instances is round(density*cols*rows).
      types       optional restriction of the primitive pool to a subset of
                  ``PRIMITIVE_TYPES`` (composition freedom, SI-018). Default (None)
                  draws uniformly from all five, the audit-neutral dialect. A
                  single-type restriction builds a same-ink/different-composition
                  impostor for the SI-022/SI-026 distinctiveness test; the genome
                  (grid, alphabet, rhythm, overprint, ground) is untouched.

    Surface is (H, W, 3) BGR uint8 on a white ground, built by multiplying flat
    ink layers (exact overprint, depth capped at 2 -- module docstring).
    """
    if not (0.0 < density <= 1.0):
        raise ValueError("density must be in (0, 1]")

    rng = np.random.default_rng(seed)
    master = master_inks(sheet)
    master_hexes = [hx for hx, _ in master]

    if ink_subset is None:
        subset = select_ink_subset(sheet, rng)
        ink_hexes = [hx for hx, _ in subset]
        ink_bgrs = [bgr for _, bgr in subset]
    else:
        ink_hexes = [hx.upper() for hx in ink_subset]
        lut = dict(master)
        ink_bgrs = [_hex_to_bgr(hx) if hx not in lut else lut[hx] for hx in ink_hexes]

    # master-set index of each subset ink, for canonicalising overlap pairs.
    master_pos = {hx: i for i, hx in enumerate(master_hexes)}
    master_index = np.array(
        [master_pos.get(hx, len(master_hexes) + i) for i, hx in enumerate(ink_hexes)],
        dtype=np.int64,
    )

    h, w = rows * module_px, cols * module_px
    acc = np.full((h, w, 3), 255, dtype=np.int64)  # white ground (audit s2)
    depth = np.zeros((h, w), dtype=np.int8)
    label_first = np.full((h, w), -1, dtype=np.int64)
    label_second = np.full((h, w), -1, dtype=np.int64)

    if types is None:
        type_pool = PRIMITIVE_TYPES
    else:
        type_pool = tuple(t for t in PRIMITIVE_TYPES if t in set(types))
        if not type_pool:
            raise ValueError("types must name at least one of PRIMITIVE_TYPES")

    n_primitives = max(1, round(density * cols * rows))
    primitives = []
    for _ in range(n_primitives):
        type_ = type_pool[int(rng.integers(len(type_pool)))]
        ink_k = int(rng.integers(len(ink_hexes)))
        mask, cells, params = _place_primitive(rng, rows, cols, module_px, type_)

        # Overprint depth cap: only print where fewer than MAX_DEPTH inks are set.
        printable = mask & (depth < MAX_DEPTH)
        if printable.any():
            at0 = printable & (depth == 0)
            at1 = printable & (depth == 1)
            label_first[at0] = ink_k
            label_second[at1] = ink_k
            acc[printable] = multiply(acc[printable], ink_bgrs[ink_k])
            depth[printable] += 1

        primitives.append(
            PrimitiveRecord(type=type_, cells=cells, ink=ink_hexes[ink_k], params=params)
        )

    surface = acc.astype(np.uint8)

    counts = {t: 0 for t in PRIMITIVE_TYPES}
    for p in primitives:
        counts[p.type] += 1

    overlaps = _summarise_overlaps(
        depth, label_first, label_second, ink_hexes, ink_bgrs, master_index
    )

    truth = GroundTruth(
        module_px=module_px,
        cols=cols,
        rows=rows,
        ink_subset=ink_hexes,
        primitives=primitives,
        primitive_counts=counts,
        overlaps=overlaps,
        depth=depth,
        label_first=label_first,
        label_second=label_second,
    )
    return surface, truth


def render(sheet: dict, **params) -> np.ndarray:
    """Render a grid composition; return (H, W, 3) BGR uint8 (see render_with_truth)."""
    surface, _ = render_with_truth(sheet, **params)
    return surface


def render_png(sheet: dict, path, **params) -> np.ndarray:
    """Render and write a PNG to ``path``. Returns the rendered BGR array.

    Byte-identical for identical sheet + params (README principle 4).
    """
    import cv2

    surface = render(sheet, **params)
    ok = cv2.imwrite(str(path), surface)
    if not ok:
        raise IOError(f"failed to write PNG to {path}")
    return surface


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a grid composition surface from a grammar sheet."
    )
    parser.add_argument("sheet", help="path to a grammar sheet YAML")
    parser.add_argument("out", help="output PNG path")
    parser.add_argument("--cols", type=int, default=12, help="grid columns")
    parser.add_argument("--rows", type=int, default=8, help="grid rows")
    parser.add_argument("--module", type=int, default=72, help="module M in px")
    parser.add_argument("--seed", type=int, default=0, help="instance seed")
    parser.add_argument("--density", type=float, default=0.45, help="composition density")
    args = parser.parse_args(argv)

    from sheets import load_sheet

    sheet = load_sheet(args.sheet)
    surface, truth = render_with_truth(
        sheet,
        cols=args.cols,
        rows=args.rows,
        module_px=args.module,
        seed=args.seed,
        density=args.density,
    )
    render_png(
        sheet,
        args.out,
        cols=args.cols,
        rows=args.rows,
        module_px=args.module,
        seed=args.seed,
        density=args.density,
    )
    print(
        f"wrote {args.out} ({args.cols}x{args.rows} cells, module {args.module}px, "
        f"{len(truth.primitives)} primitives, inks {truth.ink_subset})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
