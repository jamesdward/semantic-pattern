"""Fragment sampler for bar-cascade surfaces.

A *fragment* (spec terminology s2) is any partial view of a surface. The battery
uses this sampler to cut ground-truthed fragments out of generated surfaces so
the recogniser can be scored on how much of the cascade a fragment captures.

Audit 001 s3 is the reason band-boundary counting matters: a fragment's
identifying power is concentrated in the *transitions* -- a fragment must span
two or more band boundaries to observe the frequency ratio and phase rule
jointly. ``FragmentInfo`` therefore records, as ground truth, how many band
boundaries the window spans; the recogniser's confidence should track it.

All randomness flows through a numpy Generator passed in by the caller
(``np.random.default_rng(seed)`` at the call site) -- never global state --
so fragment sampling is reproducible (README principle 4).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import cv2
import numpy as np

# Window aspect is jittered by up to this factor either way, so fragments are
# not all the same shape while area stays pinned to ``frac``.
_ASPECT_JITTER = 0.25


@dataclass
class FragmentInfo:
    """Ground truth for one sampled fragment (see module docstring).

    Pixel coordinates are in the SOURCE surface's frame. For a rotated fragment,
    ``origin_xy`` / ``size_wh`` describe the axis-aligned bounding box of the
    sampling window and ``center_xy`` its centre; band coordinates are computed
    from that bounding box.
    """

    frac: float                        # requested area fraction (0, 1]
    origin_xy: tuple                   # (x, y) top-left of the window bbox, px
    size_wh: tuple                     # (w, h) returned fragment size, px
    center_xy: tuple                   # (x, y) window centre in source px
    rotation_deg: float                # rotation applied about the centre
    area_frac_actual: float            # realised area / source area
    # Band-coordinate ground truth (audit 001 s3). Populated when module_px and
    # n_bands are supplied; otherwise None (the surface array alone cannot say
    # where the band boundaries are -- see SPEC-ISSUES SI-012).
    band_span: Optional[tuple] = None          # (first_band, last_band) inclusive
    band_boundaries_spanned: Optional[int] = None  # interior boundaries in window
    module_px: Optional[int] = None
    n_bands: Optional[int] = None

    def to_dict(self) -> dict:
        """Plain-dict form for the battery's JSON manifests."""
        return asdict(self)


def band_boundaries_spanned(x_left, x_right, module_px, n_bands):
    """Return (count, (first_band, last_band)) for the window x-range.

    Interior band boundaries sit at x = k * module_px for k = 1 .. n_bands-1
    (audit 001 s1: band boundaries are vertical lines a module apart). A boundary
    is spanned when it lies strictly inside (x_left, x_right).
    """
    first_band = int(np.clip(x_left // module_px, 0, n_bands - 1))
    # x_right is an exclusive edge; the rightmost covered pixel is x_right - 1.
    last_band = int(np.clip((x_right - 1) // module_px, 0, n_bands - 1))
    count = 0
    for k in range(1, n_bands):
        boundary = k * module_px
        if x_left < boundary < x_right:
            count += 1
    return count, (first_band, last_band)


def _window_size(surface_h, surface_w, frac, rng):
    """Pick a window (w, h) of area ~= frac * area, aspect ~ surface aspect."""
    if not (0.0 < frac <= 1.0):
        raise ValueError("frac must be in (0, 1]")
    area = frac * surface_h * surface_w
    base_aspect = surface_w / surface_h  # w / h
    jitter = np.exp(rng.uniform(-_ASPECT_JITTER, _ASPECT_JITTER))
    aspect = base_aspect * jitter
    h = int(round(np.sqrt(area / aspect)))
    w = int(round(aspect * h))
    w = int(np.clip(w, 1, surface_w))
    h = int(np.clip(h, 1, surface_h))
    return w, h


def sample_fragment(
    surface: np.ndarray,
    *,
    frac: float,
    rng: np.random.Generator,
    rotation_deg: float = 0.0,
    module_px: Optional[int] = None,
    n_bands: Optional[int] = None,
):
    """Crop a random window of ``surface`` whose area is ``frac`` of the whole.

    Returns (fragment, FragmentInfo). The window aspect is roughly the surface
    aspect, jittered by ``rng``. With ``rotation_deg`` the window is rotated
    about its centre and sampled from the rotated surface so the fragment has no
    out-of-image border pixels (the centre is constrained so the rotated window
    stays inside the surface). ``module_px``/``n_bands`` (optional) enable the
    band-coordinate ground truth in FragmentInfo (audit 001 s3).

    All randomness comes from ``rng``; same Generator state => same crop.
    """
    if surface.ndim != 3:
        raise ValueError("surface must be an (H, W, 3) array")
    H, W = surface.shape[:2]
    w, h = _window_size(H, W, frac, rng)

    if abs(rotation_deg) < 1e-9:
        # Axis-aligned crop.
        x0 = int(rng.integers(0, W - w + 1))
        y0 = int(rng.integers(0, H - h + 1))
        fragment = surface[y0 : y0 + h, x0 : x0 + w].copy()
        cx, cy = x0 + w / 2.0, y0 + h / 2.0
        origin = (x0, y0)
        x_left, x_right = x0, x0 + w
    else:
        # Rotated window: constrain the centre so the window's rotated bounding
        # box fits inside the surface, warp the surface about that centre, then
        # crop the now-axis-aligned window.
        rad = np.deg2rad(rotation_deg)
        cos_a, sin_a = abs(np.cos(rad)), abs(np.sin(rad))
        margin_x = (w / 2.0) * cos_a + (h / 2.0) * sin_a
        margin_y = (w / 2.0) * sin_a + (h / 2.0) * cos_a
        if margin_x > W / 2.0 or margin_y > H / 2.0:
            raise ValueError(
                "rotated window does not fit in the surface; reduce frac or "
                "rotation_deg"
            )
        cx = float(rng.uniform(margin_x, W - margin_x))
        cy = float(rng.uniform(margin_y, H - margin_y))
        # Warp the surface by -rotation_deg about the centre (so the tilted window
        # becomes axis-aligned) AND translate so the window centre lands at
        # (w/2, h/2) of a w x h output -- i.e. frame the warp directly onto the
        # window. warpAffine renders the whole w x h window in one pass, so the
        # returned fragment is ALWAYS exactly (h, w).
        #
        # This replaces the earlier "warp to a full W x H canvas, then crop an
        # axis-aligned w x h box at (cx-w/2, cy-h/2)" approach, which had a bug
        # (SI-017 gap 2): near 90 deg a window wider than it is tall has
        # margin_x ~= h/2 < w/2, so cx could be < w/2 and the crop's left edge
        # x0 = round(cx - w/2) went NEGATIVE; Python's negative-index slicing then
        # read x0 as W+x0, producing an empty (zero-size) window. Rendering the
        # window directly cannot slice negatively, so no rotation 0-360 degenerates.
        # For a window that already fit under the old code the output is
        # pixel-equivalent (same rotation, same centre, just framed to the window).
        M = cv2.getRotationMatrix2D((cx, cy), -rotation_deg, 1.0)
        M[0, 2] += (w / 2.0) - cx
        M[1, 2] += (h / 2.0) - cy
        fragment = cv2.warpAffine(
            surface,
            M,
            (w, h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        origin = (int(round(cx - margin_x)), int(round(cy - margin_y)))
        x_left = int(round(cx - margin_x))
        x_right = int(round(cx + margin_x))

    fh, fw = fragment.shape[:2]
    info = FragmentInfo(
        frac=float(frac),
        origin_xy=origin,
        size_wh=(fw, fh),
        center_xy=(float(cx), float(cy)),
        rotation_deg=float(rotation_deg),
        area_frac_actual=(fw * fh) / float(H * W),
    )

    if module_px is not None and n_bands is not None:
        count, span = band_boundaries_spanned(x_left, x_right, module_px, n_bands)
        info.band_span = span
        info.band_boundaries_spanned = count
        info.module_px = int(module_px)
        info.n_bands = int(n_bands)

    return fragment, info
