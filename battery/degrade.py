"""Deterministic degradation transforms for the synthetic test battery.

Each transform is a *pure* function ``(image, param[, seed]) -> image`` that
models one hostile field condition from the L3 conformance battery (spec s11:
"print, camera, light, angle, damage"). They are the empirical machinery for
testing L2/L3 recognition: apply a known, parameterised distortion to a
ground-truthed fragment, then ask the recogniser whether it still identifies.

Every transform is deterministic -- same image + same params (+ same seed, where
one is taken) produce a byte-identical output array -- so a battery run is
reproducible (README principle 4, spec s8 "reproducible"). Shape and dtype are
always preserved: a degradation must not change (H, W, 3) uint8, only the pixel
values, so the downstream measurers see a normal surface.

Axes modelled:
  * ``gaussian_blur``   -- optical/defocus blur and downscale-reupscale softening.
  * ``jpeg_roundtrip``  -- lossy capture/transport compression (block + chroma).
  * ``brightness``      -- global exposure shift (under/over-exposed capture).
  * ``white_balance``   -- per-channel gain (camera/illuminant colour cast).
  * ``perspective_warp``-- off-axis camera angle (corner jitter ~ strength).
  * ``identity``        -- the no-op control arm.
"""

from __future__ import annotations

import cv2
import numpy as np


def _check(image: np.ndarray) -> None:
    """Guard: degradations only accept (H, W, 3) uint8 BGR surfaces."""
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("degradation input must be (H, W, 3) BGR")
    if image.dtype != np.uint8:
        raise ValueError("degradation input must be uint8")


def identity(image: np.ndarray) -> np.ndarray:
    """The control arm: return an untouched copy (no degradation)."""
    _check(image)
    return image.copy()


def gaussian_blur(image: np.ndarray, sigma: float) -> np.ndarray:
    """Isotropic Gaussian blur with standard deviation ``sigma`` pixels.

    Models optical defocus / low capture resolution. ``sigma <= 0`` is the
    identity. The kernel size is derived from sigma by OpenCV (ksize=(0,0)); the
    result is deterministic and reduces gradient (edge) energy monotonically in
    sigma, which the unit test asserts as the sane effect direction.
    """
    _check(image)
    if sigma <= 0:
        return image.copy()
    return cv2.GaussianBlur(image, (0, 0), sigmaX=float(sigma), sigmaY=float(sigma))


def jpeg_roundtrip(image: np.ndarray, quality: int) -> np.ndarray:
    """Encode to JPEG at ``quality`` (0..100) and decode back.

    Models lossy capture/transport: block artefacts and chroma subsampling.
    Lower quality => more loss. Deterministic (libjpeg is a fixed transform for a
    fixed quality). Quality 100 is near-identity but not byte-exact -- JPEG is
    lossy even at 100 -- so we never treat it as the control (that is
    ``identity``).
    """
    _check(image)
    q = int(np.clip(quality, 1, 100))
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), q])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    out = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return out


def brightness(image: np.ndarray, scale: float) -> np.ndarray:
    """Global exposure multiply by ``scale`` (1.0 = identity), clipped to [0,255].

    Models under- (scale<1) or over-exposure (scale>1). The two-colour Otsu
    classifier is exposure-robust to a point; this axis probes where that breaks.
    """
    _check(image)
    out = np.clip(image.astype(np.float32) * float(scale), 0.0, 255.0)
    return out.astype(np.uint8)


def white_balance(image: np.ndarray, gains) -> np.ndarray:
    """Per-channel gain ``gains = (b, g, r)`` (1,1,1 = identity), clipped.

    Models an illuminant / camera colour cast: each BGR channel is multiplied by
    its gain independently, shifting hue and pushing observed inks in CIE Lab.
    This is the axis the colour-pair feature (delta-E tolerance 10) most feels.
    """
    _check(image)
    g = np.asarray(gains, dtype=np.float32).reshape(1, 1, 3)
    out = np.clip(image.astype(np.float32) * g, 0.0, 255.0)
    return out.astype(np.uint8)


def perspective_warp(image: np.ndarray, strength: float, seed: int = 0) -> np.ndarray:
    """Off-axis camera warp: jitter the four corners by ~``strength`` of the size.

    Each corner is displaced by a seeded uniform draw in
    ``[-strength, strength] * (W or H)`` and the image is warped onto the jittered
    quad (``cv2.getPerspectiveTransform`` + ``warpPerspective``). ``strength = 0``
    is the identity. Border pixels are replicated rather than filled black, so a
    warped *fragment* stays pattern-filled (a black wedge would be an out-of-band
    artefact the measurer never sees on a real off-axis crop of a larger surface).

    Determinism: the corner jitter is drawn from ``np.random.default_rng(seed)``,
    so a given (image, strength, seed) always yields the same warp.
    """
    _check(image)
    if strength <= 0:
        return image.copy()
    h, w = image.shape[:2]
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    rng = np.random.default_rng(seed)
    jitter = rng.uniform(-strength, strength, size=(4, 2)).astype(np.float32)
    jitter[:, 0] *= w
    jitter[:, 1] *= h
    dst = (src + jitter).astype(np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(
        image, matrix, (w, h),
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE,
    )
