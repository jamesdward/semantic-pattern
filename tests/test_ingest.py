"""Tests for the Phase-4b print pack + real-photo ingestion path.

Two machines, tested before they are trusted (project rule):

  1. ``battery.printpack`` -- the fixed physical input. Asserts determinism
     (byte-identical pages on re-run), exact A4-at-300dpi dimensions, and that the
     bottom-margin label bounding box is DISJOINT from the pattern rectangle (the
     spec-s2 "no marks inside the pattern" guarantee, machine-checked).
  2. ``battery.ingest`` -- the real-photo path, exercised with SYNTHETIC stand-in
     "photos": generated surfaces run through perspective_warp + white_balance +
     jpeg_roundtrip and saved as .jpg, then ingested through the IDENTICAL
     recognise pipeline. Asserts the CSV/summary shape, that mild-condition
     stand-ins recognise, that the recovered implied-applied gain tracks the cast
     that was applied (question (a) plumbing), and the never-crash robustness
     (missing file / stray file / unreadable image -> row status, not exception).
"""

import csv

import cv2
import numpy as np
import pytest

from pathlib import Path

from sheets import load_sheet
from generator import cascade, grid
from battery import degrade
from battery import printpack
from battery.ingest import ingest, CSV_FIELDS, build_summary

REPO_ROOT = Path(__file__).resolve().parent.parent
SHEET_001 = REPO_ROOT / "grammars" / "bar-cascade-001.yaml"
SHEET_002 = REPO_ROOT / "grammars" / "iso-002.yaml"
TS = "2026-01-01T00:00:00+00:00"


# =========================================================================
# 1. Print pack
# =========================================================================

def test_printpack_pages_are_a4_at_300dpi():
    for spec in printpack._surface_specs():
        page, meta = printpack.build_page(spec)
        assert page.shape == (printpack.PAGE_H, printpack.PAGE_W, 3)
        assert (printpack.PAGE_W, printpack.PAGE_H) == (2480, 3508)
        assert page.dtype == np.uint8


def test_printpack_emits_six_surfaces():
    specs = printpack._surface_specs()
    ids = [s["surface_id"] for s in specs]
    assert ids == ["001-s0", "001-s1", "001-s2", "002-s0", "002-s1", "002-s2"]
    grammars = {s["grammar"] for s in specs}
    assert grammars == {"bar-cascade-001", "iso-002"}


def test_printpack_label_bbox_disjoint_from_pattern():
    """Spec s2: no marks inside the pattern. The bottom-margin label must never
    touch the pattern rectangle -- assert the two bounding boxes are disjoint."""
    for spec in printpack._surface_specs():
        _page, meta = printpack.build_page(spec)
        pat = meta["pattern_bbox"]
        lab = meta["label_bbox"]
        assert printpack._bboxes_disjoint(pat, lab), spec["surface_id"]
        # And specifically: the label sits strictly BELOW the pattern.
        assert lab[1] >= pat[3], spec["surface_id"]


def test_printpack_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    printpack.generate(a, photos_dir=a / "photos")
    printpack.generate(b, photos_dir=b / "photos")
    for name in ("001-s0.png", "001-s1.png", "002-s0.png", "002-s2.png"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_printpack_generate_writes_pack_and_manifest(tmp_path):
    photos = tmp_path / "photos"
    summary = printpack.generate(tmp_path / "pack", photos_dir=photos)
    assert len(summary["pages"]) == 6
    assert (tmp_path / "pack" / "INSTRUCTIONS.md").exists()
    assert (photos / "manifest.template.yaml").exists()
    for p in summary["pages"]:
        assert Path(p["png"]).exists()


# =========================================================================
# 2. Ingest -- synthetic stand-in photos
# =========================================================================

def _save_jpg(path, image, quality=92):
    cv2.imwrite(str(path), image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])


def _make_photos(folder):
    """Write four synthetic stand-in 'photos' and return the manifest text.

    Two mild-condition captures (one per grammar) that must recognise, plus a
    harsher 001 capture, plus a manifest entry for a file that does not exist.
    """
    folder = Path(folder)
    s1 = load_sheet(SHEET_001)
    s2 = load_sheet(SHEET_002)

    # Render at test-friendly sizes (fast) that still recognise clean.
    surf1 = cascade.render(s1, n_bands=5, module_px=200, seed=0)
    surf2 = grid.render(s2, cols=12, rows=8, module_px=72, seed=0)

    # Mild 001: tiny warp, near-neutral cast, high-quality jpeg.
    mild1 = degrade.jpeg_roundtrip(
        degrade.white_balance(degrade.perspective_warp(surf1, 0.01, seed=1),
                              (1.05, 1.0, 0.95)), 92)
    # Mild 002: tiny warp, a warm cast the relationship path should recover.
    mild2 = degrade.jpeg_roundtrip(
        degrade.white_balance(degrade.perspective_warp(surf2, 0.01, seed=2),
                              (1.1, 1.0, 0.9)), 92)
    # Harsher 001: stronger warp + heavier compression.
    harsh1 = degrade.jpeg_roundtrip(
        degrade.perspective_warp(surf1, 0.05, seed=3), 40)

    _save_jpg(folder / "IMG_1.jpg", mild1)
    _save_jpg(folder / "IMG_2.jpg", mild2)
    _save_jpg(folder / "IMG_3.jpg", harsh1)

    manifest = folder / "manifest.yaml"
    manifest.write_text(
        "surface_id: null\n"
        "photos:\n"
        "  - file: IMG_1.jpg\n"
        "    surface_id: 001-s0\n"
        "    grammar: bar-cascade-001\n"
        "    conditions: {lighting: daylight, angle_deg: 0, distance: fills_frame,"
        " printer: TestPrinter, paper: plain}\n"
        "    notes: mild\n"
        "  - file: IMG_2.jpg\n"
        "    surface_id: 002-s0\n"
        "    grammar: iso-002\n"
        "    conditions: {lighting: warm_indoor, angle_deg: 0, distance: fills_frame,"
        " printer: TestPrinter, paper: plain}\n"
        "    notes: mild\n"
        "  - file: IMG_3.jpg\n"
        "    surface_id: 001-s0\n"
        "    grammar: bar-cascade-001\n"
        "    conditions: {lighting: cool_led_or_shade, angle_deg: 60, distance: far_2m,"
        " printer: TestPrinter, paper: plain}\n"
        "    notes: harsh\n"
        "  - file: MISSING.jpg\n"
        "    surface_id: 001-s1\n"
        "    grammar: bar-cascade-001\n"
    )
    return manifest


def test_ingest_end_to_end_shape_and_recognition(tmp_path):
    manifest = _make_photos(tmp_path)
    out = tmp_path / "out"
    rows = ingest(tmp_path, manifest, out_dir=out, timestamp=TS)

    # One row per manifest entry (4).
    by_file = {r["file"]: r for r in rows}
    assert set(by_file) >= {"IMG_1.jpg", "IMG_2.jpg", "IMG_3.jpg", "MISSING.jpg"}

    # CSV + summary exist with the declared shape.
    with open(out / "raw_results.csv", newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == CSV_FIELDS
        csv_rows = list(reader)
    assert len(csv_rows) == len(rows)
    summary = (out / "summary.md").read_text()
    assert "Phase-4b questions" in summary
    for tag in ("(a)", "(b)", "(c)"):
        assert tag in summary

    # Mild stand-ins recognise: 001 identified, 002 at least candidate.
    assert by_file["IMG_1.jpg"]["status"] == "ok"
    assert by_file["IMG_1.jpg"]["top_sheet"] == "bar-cascade-001"
    assert by_file["IMG_1.jpg"]["verdict"] in ("identified", "candidate")
    assert by_file["IMG_2.jpg"]["status"] == "ok"
    assert by_file["IMG_2.jpg"]["top_sheet"] == "iso-002"
    assert by_file["IMG_2.jpg"]["verdict"] in ("identified", "candidate")

    # Missing file recorded as a status, never a crash.
    assert by_file["MISSING.jpg"]["status"] == "missing"
    assert by_file["MISSING.jpg"]["error"]


def test_ingest_records_implied_gain_for_grid(tmp_path):
    """Question (a) plumbing: the recovered implied-applied gain tracks the cast
    that was applied to the 002 stand-in (warm cast (1.1, 1.0, 0.9))."""
    manifest = _make_photos(tmp_path)
    rows = ingest(tmp_path, manifest, timestamp=TS)
    grid_row = next(r for r in rows if r["file"] == "IMG_2.jpg")
    b = float(grid_row["ink_implied_applied_gain_b"])
    g = float(grid_row["ink_implied_applied_gain_g"])
    r = float(grid_row["ink_implied_applied_gain_r"])
    # Recovered cast is in the right direction: blue boosted, red cut, green ~1.
    assert b > 1.0 > r
    assert g == pytest.approx(1.0, abs=0.1)
    assert str(grid_row["ink_gain_in_bounds"]).lower() == "true"


def test_ingest_flags_stray_and_unreadable(tmp_path):
    # A stray image not in the manifest, and a corrupt file that will not decode.
    (tmp_path / "stray.jpg").write_bytes(cv2.imencode(
        ".jpg", np.full((32, 32, 3), 127, np.uint8))[1].tobytes())
    (tmp_path / "broken.png").write_bytes(b"not a real image")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "photos:\n"
        "  - file: broken.png\n"
        "    surface_id: 001-s0\n"
        "    grammar: bar-cascade-001\n"
    )
    rows = ingest(tmp_path, manifest)  # must not raise
    by_file = {r["file"]: r for r in rows}
    assert by_file["broken.png"]["status"] == "unreadable"
    assert by_file["stray.jpg"]["status"] == "not_in_manifest"


def test_ingest_empty_manifest_gives_insufficient_summary(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("photos: []\n")
    out = tmp_path / "out"
    rows = ingest(tmp_path, manifest, out_dir=out, timestamp=TS)
    assert rows == []
    summary = (out / "summary.md").read_text()
    assert "insufficient data" in summary


def test_build_summary_marks_insufficient_when_nothing_recognised():
    rows = [{"file": "x.jpg", "status": "missing", "grammar": "iso-002",
             "verdict": "", "aggregate": ""}]
    summary = build_summary(rows, TS)
    # All three questions fall back to insufficient data with no ok rows.
    assert summary.count("insufficient data") >= 3
