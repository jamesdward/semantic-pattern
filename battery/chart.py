"""Minimal deterministic charts for the battery report (numpy + cv2 only).

matplotlib is not a project dependency, so this is a small hand-rolled plotter:
white canvas, drawn axes with labelled ticks, coloured polylines with an optional
shaded +/- band, and a legend. Two chart kinds cover the report's needs:

  * ``line_chart``  -- mean(+/-band) vs x, one line per series, optional horizontal
                       threshold rules (the 0.70 / 0.40 verdict lines).
  * ``hist_chart``  -- overlaid step histograms (genuine vs impostor distributions).

Everything is deterministic (fixed canvas, fixed rounding) so a battery run
produces byte-identical PNGs from identical inputs (README principle 4). Output
is BGR uint8 written with ``cv2.imwrite``; legibility is the priority since these
go in a published report.
"""

from __future__ import annotations

import cv2
import numpy as np

# A fixed, colour-blind-friendlyish BGR palette (deterministic series colours).
PALETTE = [
    (180, 90, 40),    # blue
    (40, 140, 220),   # orange
    (60, 160, 60),    # green
    (60, 60, 200),    # red
    (160, 80, 160),   # purple
    (120, 120, 60),   # teal
    (30, 30, 30),     # near-black
]

_W, _H = 1100, 720          # canvas size
_ML, _MR, _MT, _MB = 110, 260, 70, 90   # margins (right margin holds the legend)
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _canvas():
    return np.full((_H, _W, 3), 255, np.uint8)


def _plot_box():
    """Return (x0, y0, x1, y1) pixel bounds of the plotting rectangle."""
    return _ML, _MT, _W - _MR, _H - _MB


def _mapper(xlim, ylim):
    """Return a function (x, y) -> (px, py) mapping data coords to pixels."""
    x0, y0, x1, y1 = _plot_box()
    xmin, xmax = xlim
    ymin, ymax = ylim
    xspan = (xmax - xmin) or 1.0
    yspan = (ymax - ymin) or 1.0

    def to_px(x, y):
        px = x0 + (x - xmin) / xspan * (x1 - x0)
        py = y1 - (y - ymin) / yspan * (y1 - y0)
        return int(round(px)), int(round(py))

    return to_px


def _draw_frame(img, xlim, ylim, xlabel, ylabel, title, xticks, yticks):
    x0, y0, x1, y1 = _plot_box()
    cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 0), 1)
    to_px = _mapper(xlim, ylim)
    # x ticks + labels
    for xt in xticks:
        px, _ = to_px(xt, ylim[0])
        cv2.line(img, (px, y1), (px, y1 + 6), (0, 0, 0), 1)
        label = f"{xt:g}"
        (tw, _), _ = cv2.getTextSize(label, _FONT, 0.5, 1)
        cv2.putText(img, label, (px - tw // 2, y1 + 24), _FONT, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    # y ticks + labels
    for yt in yticks:
        _, py = to_px(xlim[0], yt)
        cv2.line(img, (x0 - 6, py), (x0, py), (0, 0, 0), 1)
        label = f"{yt:g}"
        (tw, th), _ = cv2.getTextSize(label, _FONT, 0.5, 1)
        cv2.putText(img, label, (x0 - 12 - tw, py + th // 2), _FONT, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    # axis titles
    (tw, _), _ = cv2.getTextSize(xlabel, _FONT, 0.6, 1)
    cv2.putText(img, xlabel, ((x0 + x1) // 2 - tw // 2, _H - 24), _FONT, 0.6, (0, 0, 0), 1, cv2.LINE_AA)
    # y label drawn rotated
    ylab_img = np.full((30, 360, 3), 255, np.uint8)
    cv2.putText(ylab_img, ylabel, (0, 22), _FONT, 0.6, (0, 0, 0), 1, cv2.LINE_AA)
    ylab_img = cv2.rotate(ylab_img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    yh, yw = ylab_img.shape[:2]
    oy = (y0 + y1) // 2 - yh // 2
    img[oy:oy + yh, 18:18 + yw] = np.minimum(img[oy:oy + yh, 18:18 + yw], ylab_img)
    # title
    (tw, _), _ = cv2.getTextSize(title, _FONT, 0.7, 2)
    cv2.putText(img, title, ((x0 + x1) // 2 - tw // 2, 40), _FONT, 0.7, (0, 0, 0), 2, cv2.LINE_AA)


def _legend(img, entries):
    """Draw a legend in the right margin. ``entries`` = [(label, bgr), ...]."""
    x0, y0, x1, y1 = _plot_box()
    lx = x1 + 24
    ly = y0 + 20
    for label, colour in entries:
        cv2.line(img, (lx, ly), (lx + 26, ly), colour, 3, cv2.LINE_AA)
        cv2.putText(img, label, (lx + 34, ly + 5), _FONT, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
        ly += 26


def _nice_ticks(vmin, vmax, n=6):
    return [round(vmin + (vmax - vmin) * i / n, 4) for i in range(n + 1)]


def line_chart(path, series, x_values, *, xlabel, ylabel, title,
               ylim=(0.0, 1.0), hlines=None, xlim=None):
    """Draw a multi-series line chart with optional +/- bands and threshold rules.

    Parameters
      series    dict label -> {"mean": [...], "band": [...] optional}. Points whose
                mean is None are treated as gaps (not plotted). ``band`` (same
                length) draws a shaded +/- region.
      x_values  the x coordinate of each point (shared across series).
      hlines    list of (y, label) horizontal reference rules (e.g. verdict lines).
    """
    img = _canvas()
    xs = list(x_values)
    if xlim is None:
        xlim = (min(xs), max(xs))
    xticks = xs if len(xs) <= 10 else _nice_ticks(xlim[0], xlim[1])
    yticks = _nice_ticks(ylim[0], ylim[1])
    _draw_frame(img, xlim, ylim, xlabel, ylabel, title, xticks, yticks)
    to_px = _mapper(xlim, ylim)

    # horizontal threshold rules
    for (yv, lab) in (hlines or []):
        _, py = to_px(xlim[0], yv)
        x0, _, x1, _ = _plot_box()
        for xx in range(x0, x1, 12):     # dashed
            cv2.line(img, (xx, py), (min(xx + 6, x1), py), (120, 120, 120), 1, cv2.LINE_AA)
        cv2.putText(img, lab, (x1 - 150, py - 6), _FONT, 0.45, (110, 110, 110), 1, cv2.LINE_AA)

    legend_entries = []
    for i, (label, data) in enumerate(series.items()):
        colour = PALETTE[i % len(PALETTE)]
        legend_entries.append((label, colour))
        means = data["mean"]
        bands = data.get("band")
        # shaded band as a filled polygon over contiguous non-None runs
        if bands is not None:
            upper, lower = [], []
            for x, m, b in zip(xs, means, bands):
                if m is None:
                    continue
                b = b or 0.0
                upper.append(to_px(x, min(ylim[1], m + b)))
                lower.append(to_px(x, max(ylim[0], m - b)))
            if len(upper) >= 2:
                poly = np.array(upper + lower[::-1], np.int32)
                overlay = img.copy()
                cv2.fillPoly(overlay, [poly], colour)
                cv2.addWeighted(overlay, 0.15, img, 0.85, 0, img)
        # the mean polyline + markers
        prev = None
        for x, m in zip(xs, means):
            if m is None:
                prev = None
                continue
            p = to_px(x, m)
            cv2.circle(img, p, 3, colour, -1, cv2.LINE_AA)
            if prev is not None:
                cv2.line(img, prev, p, colour, 2, cv2.LINE_AA)
            prev = p

    _legend(img, legend_entries)
    cv2.imwrite(str(path), img)
    return path


def bar_chart(path, groups, series, *, xlabel, ylabel, title, ymax=None):
    """Grouped bar chart (added for the cross-grammar confusion summary).

    Reuses the same canvas/frame/legend machinery as ``line_chart``.

    Parameters
      groups   list of x-category labels (one cluster of bars each).
      series   dict label -> list of values (length == len(groups)); one coloured
               bar per series within each group.
      ymax     optional y-axis top; default 1.15 x the largest value.
    """
    img = _canvas()
    n_groups = max(1, len(groups))
    n_series = max(1, len(series))
    all_vals = [float(v) for vals in series.values() for v in vals]
    top = ymax if ymax is not None else (max(all_vals) if all_vals else 1.0) * 1.15
    top = max(top, 1.0)
    ylim = (0.0, top)
    xlim = (0.0, float(n_groups))
    yticks = _nice_ticks(0.0, top)
    _draw_frame(img, xlim, ylim, xlabel, ylabel, title, [], yticks)
    to_px = _mapper(xlim, ylim)
    x0, _y0, x1, y1 = _plot_box()

    group_w = (x1 - x0) / n_groups
    bar_w = group_w * 0.8 / n_series
    legend_entries = []
    for si, (label, vals) in enumerate(series.items()):
        colour = PALETTE[si % len(PALETTE)]
        legend_entries.append((label, colour))
        for gi, v in enumerate(vals):
            gx = x0 + gi * group_w + group_w * 0.1 + si * bar_w
            ytop = to_px(0.0, float(v))[1]
            cv2.rectangle(img, (int(round(gx)), int(ytop)),
                          (int(round(gx + bar_w - 2)), int(y1)), colour, -1)
            if v:  # count label above the bar
                cv2.putText(img, f"{int(v)}", (int(round(gx)), int(ytop) - 4),
                            _FONT, 0.4, (0, 0, 0), 1, cv2.LINE_AA)
    for gi, g in enumerate(groups):
        cx = x0 + gi * group_w + group_w / 2.0
        (tw, _), _ = cv2.getTextSize(g, _FONT, 0.42, 1)
        cv2.putText(img, g, (int(round(cx - tw / 2)), int(y1) + 20),
                    _FONT, 0.42, (0, 0, 0), 1, cv2.LINE_AA)
    _legend(img, legend_entries)
    cv2.imwrite(str(path), img)
    return path


def hist_chart(path, data, *, xlabel, title, bins=20, xlim=(0.0, 1.0), vlines=None):
    """Overlaid normalised step histograms, one per label in ``data``.

    ``data`` = dict label -> 1D array of values. Each histogram is normalised to
    its own peak so distributions of different sample sizes stay comparable.
    ``vlines`` = [(x, label)] draws vertical reference rules (verdict thresholds).
    """
    img = _canvas()
    edges = np.linspace(xlim[0], xlim[1], bins + 1)
    counts = {}
    peak = 1
    for label, vals in data.items():
        arr = np.asarray([v for v in vals if v is not None], dtype=float)
        h, _ = np.histogram(arr, bins=edges)
        counts[label] = h
        peak = max(peak, int(h.max()) if h.size else 1)
    ylim = (0.0, 1.0)
    xticks = _nice_ticks(xlim[0], xlim[1])
    yticks = _nice_ticks(0.0, 1.0)
    _draw_frame(img, xlim, ylim, xlabel, "relative frequency", title, xticks, yticks)
    to_px = _mapper(xlim, ylim)

    for (xv, lab) in (vlines or []):
        px, _ = to_px(xv, 0.0)
        _, y0, _, y1 = _plot_box()
        for yy in range(y0, y1, 12):
            cv2.line(img, (px, yy), (px, min(yy + 6, y1)), (120, 120, 120), 1, cv2.LINE_AA)
        cv2.putText(img, lab, (px + 4, y0 + 14), _FONT, 0.45, (110, 110, 110), 1, cv2.LINE_AA)

    legend_entries = []
    for i, (label, h) in enumerate(counts.items()):
        colour = PALETTE[i % len(PALETTE)]
        legend_entries.append((f"{label} (n={int(h.sum())})", colour))
        norm = h / peak
        pts = []
        for j in range(bins):
            x_lo, x_hi = edges[j], edges[j + 1]
            y = norm[j]
            pts.append(to_px(x_lo, y))
            pts.append(to_px(x_hi, y))
        for k in range(len(pts) - 1):
            cv2.line(img, pts[k], pts[k + 1], colour, 2, cv2.LINE_AA)

    _legend(img, legend_entries)
    cv2.imwrite(str(path), img)
    return path
