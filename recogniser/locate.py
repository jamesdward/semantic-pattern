"""Scene localisation + perspective rectification (spec 8 step 1; SI-025).

The recogniser's existing measurers (``recogniser/measure.py``,
``recogniser/measure_grid.py``) assume the image handed to them *is* the pattern:
every synthetic battery fragment is 100% surface. A real photograph is mostly
*scene* -- a green print square sits inside a white page, inside a dark screen
bezel, on a desk (exp-003 pilot). Feeding the whole frame to a two-colour Otsu
classifier splits page-white vs bezel-dark instead of ink vs ink, and every
downstream number is garbage (pilot: 0/24 recognised, aggregate ~0.02). Spec 8
step 1 lists normalisation (perspective, scale, orientation) but never says
"first find the surface in the scene" -- recorded as SI-025 and *decided here*:
a classical locate -> rectify stage ahead of the unchanged per-family pipeline.

Two stages, both classical + deterministic (OpenCV + numpy, no learned models;
spec 8 conformance: classical measurement path):

  1. ``find_candidate_regions`` -- COLOUR-COHERENCE localisation. Cluster the
     scene coarsely and keep connected regions whose colour is compatible with
     ANY enrolled sheet's ink set (within a generous delta-E) AND that carry
     interior structure (edge energy -- a flat green wall is not a pattern).
     Sheet-conditioned localisation is legitimate: recognition already scores a
     fragment against the enrolled sheets, so using their declared inks to find
     candidate surfaces adds no information the pipeline did not already have --
     it just refuses to hunt for colours no enrolled identity uses. The full
     frame is always offered as a fallback candidate, and when a dominant
     ink-compatible region already spans ~>=90% of the frame the image is treated
     as ALREADY LOCALISED (a bare fragment): the caller then runs the byte-
     identical pre-SI-025 path, so synthetic behaviour is unchanged.

  2. ``rectify`` -- PERSPECTIVE correction. The print square sits inside a white
     page; detect a strong quadrilateral (the pattern square's own outline, or
     the page's) via contour + approxPolyDP and warp it fronto-parallel with a
     homography so the cascade bands stand vertical and the bars run horizontal.
     If no reliable quad is found the region is returned unrectified with a
     caveat in the working (honest: a missing quad is measurement loss, not a
     silent guess). The SEARCH runs on a downscaled copy for speed; the returned
     region is always full-resolution so measurement is never softened.

Determinism: the colour mask, morphology, connected components, contour
approximation and warp are all fixed transforms, so the same image yields the
same regions and the same rectification (README principle 4).
"""

from __future__ import annotations

import cv2
import numpy as np

# --- constants (v0 choices; empirical ones flagged with generalisation risk) --

# Longest side (px) the SEARCH copy is downscaled to. Localisation only needs
# coarse colour blobs, so a ~1200 px proxy of a 5712 px phone frame is ample and
# ~20x cheaper; every returned region is still cropped from the FULL-RES frame,
# so measurement precision is untouched. Principled: a scale that keeps the
# smallest pattern (~25% of frame) hundreds of px wide.
SEARCH_MAX_DIM = 1200

# Generous delta-E76 (CIE Lab) within which a pixel counts as "ink-compatible"
# with a sheet ink. EMPIRICAL (generalisation risk noted): real capture pushed
# the 001 greens 15-30 delta-E off their enrolled Lab (exp-003 pilot warm/bright
# shift), so a synthetic-tight gate (~10) would miss the very pattern we localise.
# 34 is wide enough to hold the shifted greens yet below the ~50 at which a dark
# navy bezel starts merging into dark green (measured on the pilot frames). This
# is a LOCALISATION gate only -- colour *identity* is still scored later at the
# sheet's committed delta-E tolerance, so a loose locator cannot flatter a claim.
# Risk: a different display/illuminant could shift inks past 34; a production
# locator would adapt this per capture (out of scope this phase).
INK_DELTA_E = 34.0

# A candidate connected component must hold at least this fraction of the (search)
# frame to be a surface rather than a colour speck. 0.02 keeps a far-2m pattern
# (~5-8% of frame) while dropping stray green pixels (reflections, JPEG ringing).
MIN_REGION_FRAC = 0.02

# Morphology kernel (search px) to close ink gaps within a pattern (the light/dark
# stripes leave the mask porous) and drop thin bridges to reflections. Odd; scaled
# to the search image so it is resolution-stable.
_CLOSE_FRAC = 0.02   # of the search longest side

# Interior-structure gate: a real pattern region is full of edges; a flat colour
# field (a green wall, a reflection) is not. A kept region must carry at least this
# mean Sobel-edge fraction over its bounding box on the search image. Low bar --
# this only rejects near-flat blobs, not textured ones.
MIN_EDGE_FRAC = 0.03

# Already-localised short-circuit: if the ink-compatible content spans at least
# this fraction of the frame AREA, the image is a bare fragment (synthetic
# battery, or a pre-cropped surface), not a scene. The caller then runs the
# unchanged pre-SI-025 path so synthetic claims stay byte-identical.
#
# The span is measured by a ROBUST marginal-projection bbox (not the raw ink-pixel
# bbox): a column/row counts as ink-bearing only when >= INK_MARGINAL_FRAC of it
# is ink-compatible, so a handful of stray green-ish pixels in the scene corners
# (reflections, JPEG ringing, dark surround grazing the delta-E gate) cannot
# inflate the bbox to the whole frame and false-trigger the short-circuit. On both
# synthetic families every column/row crosses the pattern (band: all ink; grid:
# the lattice spans corner to corner), so the marginal bbox is ~1.0 and they
# short-circuit; measured on the pilot frames real scenes top out at ~0.77, so 0.90
# separates them with margin.
ALREADY_LOCALISED_BBOX_FRAC = 0.90
INK_MARGINAL_FRAC = 0.05

# Rectification: the detected quad must cover at least this fraction of the region
# crop (a real pattern/page quad nearly fills its own crop; a small spurious
# contour does not) and approxPolyDP must yield exactly 4 convex corners. epsilon
# is this fraction of the contour perimeter.
RECTIFY_MIN_QUAD_FRAC = 0.55
RECTIFY_APPROX_EPS_FRAC = 0.02


# Anti-capture-moire measurement resolution (px, longest side). A photograph of a
# SCREEN carries moire between the display sub-pixel grid and the camera sensor,
# and phone captures over-sharpen edges; both inject high-frequency ripple that
# the run-length band segmenter reads as hundreds of false band boundaries
# (measured: a rectified 3000 px screen crop segments into 12-200+ "bands", ratio
# garbage; the SAME crop area-downscaled to ~800 px segments into the true 5 bands
# with ratio ~1.93). Area-downscaling the rectified region to this size before
# measurement is a deterministic low-pass that suppresses the capture ripple while
# leaving the real bar periods (tens of px) fully resolved. EMPIRICAL (generalises
# to screen capture; a higher-DPI print might warrant a larger value) -- flagged
# with generalisation risk, decided-here under SI-025's rectification stage. Only
# the SCENE path resamples; a bare fragment short-circuits and is never touched, so
# synthetic measurements are byte-identical.
MEASURE_MAX_DIM = 800


def measurement_resample(region_bgr: np.ndarray) -> np.ndarray:
    """Area-downscale a rectified region to ``MEASURE_MAX_DIM`` (anti-moire low-pass).

    Returns the region unchanged when it is already at or below the target size
    (never upscales). See ``MEASURE_MAX_DIM``: this is the capture-moire low-pass,
    applied only on the scene path, so a bare fragment (short-circuit) is untouched.
    """
    h, w = region_bgr.shape[:2]
    longest = max(h, w)
    if longest <= MEASURE_MAX_DIM:
        return region_bgr
    s = MEASURE_MAX_DIM / float(longest)
    return cv2.resize(region_bgr, (max(1, int(round(w * s))), max(1, int(round(h * s)))),
                      interpolation=cv2.INTER_AREA)


def _hex_to_lab(hex_value: str) -> np.ndarray:
    """'#RRGGBB' -> CIE Lab via the same float path the scorer uses."""
    h = hex_value.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    bgr = np.array([[[b, g, r]]], dtype=np.float32) / 255.0
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab)
    return lab[0, 0].astype(np.float64)


def sheet_ink_labs(sheets) -> list:
    """Every enrolled sheet ink as a CIE Lab triple (deduplicated by hex).

    Localisation is conditioned on the union of all enrolled ink sets: a
    candidate region is kept if it is compatible with ANY sheet's ink. This is
    the legitimate sheet-conditioning noted in the module docstring -- the
    pipeline already knows the enrolled identities.
    """
    seen = {}
    for sheet in sheets:
        for ink in sheet.get("colour_system", {}).get("inks", []) or []:
            val = ink.get("value")
            if isinstance(val, str) and val.startswith("#") and val not in seen:
                seen[val] = _hex_to_lab(val)
    return list(seen.values())


# A pixel with every channel at or above this is treated as WHITE GROUND, never
# ink -- even though a couple of enrolled inks are pale enough (iso-002's
# teal_grey #96CBC4, light_blue #95C3F9) to sit within INK_DELTA_E of pure white.
WHITE_GROUND_MIN = 245

# Minimum CIE Lab chroma (sqrt(a^2 + b^2)) for a pixel to count as ink. GROUND is
# achromatic: a printed/displayed page is near-neutral (measured chroma ~1-5 on the
# pilot frames) even when it is warm off-white BELOW the WHITE_GROUND_MIN cutoff, so
# a delta-E gate alone lets the page match iso-002's pale near-neutral inks and the
# located region balloons to the whole page (measured: duty inflated to ~0.66,
# light ink pulled to near-white). Every enrolled ink is chromatic -- the palest,
# teal_grey, measures chroma ~18.6, and the 001 greens ~32-40 -- so an 8-unit floor
# drops the page and neutral display bloom while keeping every ink. This encodes the
# ground/ink split the sheet's colour_system already declares (a saturated ink set
# on a neutral ground), and is what lets localisation key on the true pattern rather
# than its surrounding page. Localisation gate only; colour identity is still scored
# later at the sheet's committed delta-E.
CHROMA_MIN = 8.0


def _ink_mask(bgr_small: np.ndarray, ink_labs) -> np.ndarray:
    """Bool mask of pixels within ``INK_DELTA_E`` of any sheet ink (Lab delta-E76).

    Excludes GROUND: near-white pixels (all channels >= ``WHITE_GROUND_MIN``) and
    near-neutral pixels (Lab chroma < ``CHROMA_MIN``) never count as ink, so a
    white/off-white/grey page never reads as ink-compatible (see CHROMA_MIN).
    """
    lab = cv2.cvtColor(bgr_small.astype(np.float32) / 255.0, cv2.COLOR_BGR2Lab)
    flat = lab.reshape(-1, 3).astype(np.float64)
    best = np.full(flat.shape[0], np.inf)
    for il in ink_labs:
        d = np.sqrt(((flat - il) ** 2).sum(axis=1))
        best = np.minimum(best, d)
    mask = (best < INK_DELTA_E).reshape(bgr_small.shape[:2])
    chroma = np.sqrt(lab[:, :, 1] ** 2 + lab[:, :, 2] ** 2)
    white = np.all(bgr_small >= WHITE_GROUND_MIN, axis=2)
    mask &= ~white
    mask &= chroma >= CHROMA_MIN
    return mask


def _edge_fraction(bgr_region: np.ndarray) -> float:
    """Fraction of pixels carrying a strong Sobel edge (interior-structure gate)."""
    if bgr_region.size == 0:
        return 0.0
    gray = cv2.cvtColor(bgr_region, cv2.COLOR_BGR2GRAY).astype(np.float64)
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    if mag.max() <= 0:
        return 0.0
    strong = mag > (0.20 * mag.max())
    return float(strong.mean())


def find_candidate_regions(image: np.ndarray, sheets) -> list:
    """Propose ink-compatible surface regions in ``image`` (BGR uint8).

    Returns a list of ``{"region", "bbox", "working"}`` where ``region`` is a
    FULL-RES crop, ``bbox`` is ``(x, y, w, h)`` in full-res pixels, and
    ``working`` records how the region was found.

    Behaviour (see module docstring):
      * ALREADY LOCALISED -- if the ink-compatible content's bbox spans
        ``>= ALREADY_LOCALISED_BBOX_FRAC`` of the frame, returns a single region
        (the full frame) flagged ``already_localised``; the caller runs the
        byte-identical pre-SI-025 path.
      * SCENE -- otherwise returns each colour-coherent, structured region found,
        largest first.
      * NOTHING FOUND -- returns the full frame alone, flagged ``fallback`` (the
        pre-SI-025 whole-frame reading, so a claim is still produced).
    """
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be (H, W, 3) BGR uint8")
    H, W = image.shape[:2]
    full_region = {"region": image, "bbox": (0, 0, W, H)}

    ink_labs = sheet_ink_labs(sheets)
    if not ink_labs:
        # No enrolled colours to key on: nothing to localise against.
        return [{**full_region, "working": {"fallback": True,
                                            "reason": "no enrolled inks to localise against"}}]

    # 1. Downscale for the SEARCH only.
    scale = min(1.0, SEARCH_MAX_DIM / float(max(H, W)))
    if scale < 1.0:
        sw, sh = int(round(W * scale)), int(round(H * scale))
        small = cv2.resize(image, (sw, sh), interpolation=cv2.INTER_AREA)
    else:
        sw, sh = W, H
        small = image

    mask = _ink_mask(small, ink_labs).astype(np.uint8)

    # Already-localised test on the ROBUST marginal-projection bbox (a column/row
    # counts only when >= INK_MARGINAL_FRAC of it is ink-compatible, so scene
    # strays cannot inflate it). See ALREADY_LOCALISED_BBOX_FRAC.
    col_active = np.where(mask.sum(axis=0) > INK_MARGINAL_FRAC * sh)[0]
    row_active = np.where(mask.sum(axis=1) > INK_MARGINAL_FRAC * sw)[0]
    if col_active.size and row_active.size:
        span_w = int(col_active.max() - col_active.min() + 1)
        span_h = int(row_active.max() - row_active.min() + 1)
        bbox_frac = (span_w * span_h) / float(sw * sh)
    else:
        bbox_frac = 0.0
    if bbox_frac >= ALREADY_LOCALISED_BBOX_FRAC:
        return [{**full_region,
                 "working": {"already_localised": True,
                             "ink_bbox_frac": round(bbox_frac, 4),
                             "reason": "ink-compatible content spans the whole frame; "
                                       "bare fragment, unchanged pre-SI-025 path"}}]

    # 2. Clean the mask: close stripe gaps, open away thin bridges/specks.
    k = max(3, int(round(_CLOSE_FRAC * max(sw, sh)))) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel)

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    inv_scale = 1.0 / scale
    regions = []
    for lab in range(1, n_labels):
        area = int(stats[lab, cv2.CC_STAT_AREA])
        if area < MIN_REGION_FRAC * sw * sh:
            continue
        x = int(stats[lab, cv2.CC_STAT_LEFT])
        y = int(stats[lab, cv2.CC_STAT_TOP])
        w = int(stats[lab, cv2.CC_STAT_WIDTH])
        h = int(stats[lab, cv2.CC_STAT_HEIGHT])
        # Interior-structure gate on the search crop.
        edge_frac = _edge_fraction(small[y:y + h, x:x + w])
        if edge_frac < MIN_EDGE_FRAC:
            continue
        # Map the bbox back to full resolution (clamp to frame).
        fx = int(np.floor(x * inv_scale))
        fy = int(np.floor(y * inv_scale))
        fw = int(min(W - fx, np.ceil(w * inv_scale)))
        fh = int(min(H - fy, np.ceil(h * inv_scale)))
        if fw <= 0 or fh <= 0:
            continue
        regions.append({
            "region": image[fy:fy + fh, fx:fx + fw],
            "bbox": (fx, fy, fw, fh),
            "working": {"already_localised": False,
                        "source": "colour-coherence + structure",
                        "search_area_frac": round(area / float(sw * sh), 4),
                        "edge_frac": round(edge_frac, 4),
                        "ink_delta_e": INK_DELTA_E},
        })

    # Largest ink-content region first (most likely the surface); deterministic.
    regions.sort(key=lambda r: (-(r["bbox"][2] * r["bbox"][3]), r["bbox"][0], r["bbox"][1]))

    if not regions:
        return [{**full_region, "working": {"fallback": True,
                                            "reason": "no colour-coherent structured region found"}}]
    return regions


# --- perspective rectification ---------------------------------------------


def _order_quad(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left.

    Standard sum/diff ordering: TL has the smallest x+y, BR the largest; TR has
    the smallest (y-x), BL the largest. Deterministic for any convex quad.
    """
    pts = pts.astype(np.float64)
    s = pts.sum(axis=1)
    d = pts[:, 1] - pts[:, 0]
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)],
                     pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def _detect_quad(region_bgr: np.ndarray, ink_labs):
    """Largest convex 4-gon around the ink content of a region crop, or None.

    Returns the ordered corner array in FULL-RES crop coordinates, or ``None``.

    The quad is traced on a DOWNSCALED copy of the crop (the outline is a coarse,
    blob-scale feature that needs no full resolution -- a page/pattern square edge
    is hundreds of px even at the search scale), then the corners are scaled back
    to full-res so the subsequent warp still samples the full-resolution crop. This
    keeps the expensive per-pixel ink-mask off the multi-megapixel crop (measured:
    the dominant cost of the whole scene path).
    """
    h, w = region_bgr.shape[:2]
    if h < 8 or w < 8:
        return None
    ds = min(1.0, SEARCH_MAX_DIM / float(max(h, w)))
    if ds < 1.0:
        small = cv2.resize(region_bgr, (max(1, int(round(w * ds))), max(1, int(round(h * ds)))),
                           interpolation=cv2.INTER_AREA)
    else:
        small = region_bgr
    sh, swd = small.shape[:2]
    mask = _ink_mask(small, ink_labs).astype(np.uint8)
    k = max(3, int(round(0.03 * max(sh, swd)))) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    if cv2.contourArea(cnt) < RECTIFY_MIN_QUAD_FRAC * sh * swd:
        return None
    peri = cv2.arcLength(cnt, True)
    approx = cv2.approxPolyDP(cnt, RECTIFY_APPROX_EPS_FRAC * peri, True)
    if len(approx) == 4 and cv2.isContourConvex(approx):
        quad = _order_quad(approx.reshape(4, 2))
    else:
        # Fall back to the min-area rotated rectangle if the polygon is not a
        # clean quad (rounded/occluded corners). Still a genuine 4-gon fit.
        box = cv2.boxPoints(cv2.minAreaRect(cnt))
        if cv2.contourArea(box.astype(np.float32)) < RECTIFY_MIN_QUAD_FRAC * sh * swd:
            return None
        quad = _order_quad(box)
    return (quad / ds).astype(np.float32)   # corners back to full-res crop coords


def rectify(image: np.ndarray, region: dict, sheets=None) -> tuple:
    """Perspective-correct a region to fronto-parallel; return ``(warped, working)``.

    Detects the pattern square's quadrilateral inside the FULL-RES region crop
    (contour + approxPolyDP, falling back to the min-area rectangle) and warps it
    onto an axis-aligned rectangle whose size is the mean of the quad's opposite
    side lengths, so scale and aspect are preserved and the cascade bands stand
    vertical. If no reliable quad is found the crop is returned unchanged with a
    caveat -- a missing quad is measurement loss, honestly flagged, never a guess.

    ``sheets`` supplies the ink set the quad detector keys on; if omitted the
    region is returned unrectified (the detector needs a colour to trace).
    """
    crop = region["region"]
    if sheets is None:
        return crop, {"rectified": False, "caveat": "no sheets supplied for quad detection"}
    ink_labs = sheet_ink_labs(sheets)
    if not ink_labs:
        return crop, {"rectified": False, "caveat": "no enrolled inks for quad detection"}

    quad = _detect_quad(crop, ink_labs)
    if quad is None:
        return crop, {"rectified": False,
                      "caveat": "no reliable pattern quad found; measured unrectified"}

    tl, tr, br, bl = quad
    width = int(round((np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2.0))
    height = int(round((np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2.0))
    if width < 8 or height < 8:
        return crop, {"rectified": False,
                      "caveat": "degenerate quad; measured unrectified"}
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
                   dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(quad, dst)
    warped = cv2.warpPerspective(crop, matrix, (width, height),
                                 flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return warped, {"rectified": True,
                    "quad_in_crop": [[round(float(p[0]), 1), round(float(p[1]), 1)]
                                     for p in quad],
                    "output_wh": (width, height)}
