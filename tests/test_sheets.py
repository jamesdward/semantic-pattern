"""Tests for the grammar-sheet loader and validator (sheets package).

Covers the shipped sheets loading clean, plus each semantic rule the validator
enforces on top of the JSON Schema (spec 3.7 canonical peaks, relative units,
identification weight sum, relational operands) and the list_sheets sweep.
"""

import copy
from pathlib import Path

import pytest

from sheets import SheetError, list_sheets, load_sheet, validate_sheet

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAMMARS = REPO_ROOT / "grammars"


def _base_sheet():
    """A minimal but fully valid sheet dict used as the fixture for mutation.

    Mirrors bar-cascade-001's shape closely enough to exercise every check.
    """
    return {
        "spec_version": "0.1",
        "sheet": {
            "id": "test-sheet",
            "name": "Test Sheet",
            "grammar_version": "1.0.0",
            "status": "enrolled",
            "issuer": None,
            "identity_endpoint": None,
        },
        "ground": {"substrate": "white"},
        "structure": {
            "type": "band",
            "band": {"module_width_relative": 1.0, "bands_visible": 5},
        },
        "primitive_alphabet": [{"id": "bar", "form": "horizontal bar"}],
        "combination_rules": {
            "frequency_ratio": 1.94,
            "duty_cycle_light": 0.31,
            "phase_step": 0.31,
        },
        "colour_system": {
            "space": "sRGB-hex",
            "inks": [
                {"id": "dark", "value": "#2E5B30"},
                {"id": "light", "value": "#73B881"},
            ],
            "layering": "opaque",
            "relationships": ["luminance(light) > luminance(dark)"],
        },
        "variation_model": {"varies": ["duty_cycle_light"], "invariant": ["structure"]},
        "signature_locus": {
            "features": [
                {
                    "id": "cascade_ratio",
                    "measure": "period_cascade_ratio",
                    "expected": 1.94,
                    "tolerance": 0.03,
                    "role": "identification",
                    "weight": 0.5,
                    "sample_unit": "band_boundaries",
                    "canonical_peaks": [2.0, 1.5, 1.618],
                    "status": "measured",
                },
                {
                    "id": "phase_duty_identity",
                    "measure": "relation_equal",
                    "operands": ["phase_step", "duty_cycle_light"],
                    "tolerance": 0.02,
                    "role": "identification",
                    "weight": 0.5,
                    "sample_unit": "band_boundaries",
                    "status": "measured",
                },
                {
                    "id": "band_module",
                    "measure": "band_period",
                    "role": "normalisation",
                    "weight": 0,
                    "sample_unit": "bands",
                    "status": "measured",
                },
            ],
            "aggregation": {"method": "weighted_sum", "min_features": 1},
        },
    }


def test_base_fixture_is_valid():
    # Guards the negative tests: they must start from something that passes.
    validate_sheet(_base_sheet())


# --- shipped sheets ---------------------------------------------------------


@pytest.mark.parametrize("name", ["bar-cascade-001.yaml", "iso-002.yaml"])
def test_shipped_sheets_load_and_validate(name):
    sheet = load_sheet(GRAMMARS / name)
    assert sheet["signature_locus"]["features"]


def test_iso_002_is_audit_reconstruction_not_enrolled():
    # spec 6.2: only the owner may enrol; 002 is a methodology demonstration.
    sheet = load_sheet(GRAMMARS / "iso-002.yaml")
    assert sheet["sheet"]["status"] == "audit-reconstruction"


def test_iso_002_validates_despite_canonical_structure():
    # The point of 002: canonical peaks (duty 0.5, 45deg) are kept at weight 0,
    # so the canonical-peak rule does not fire and the sheet is valid.
    load_sheet(GRAMMARS / "iso-002.yaml")


# --- canonical-peak rule (spec 3.7) -----------------------------------------


def test_identification_on_canonical_peak_rejected():
    sheet = _base_sheet()
    # Cascade ratio parked exactly on the power-of-two peak, still weighted.
    sheet["signature_locus"]["features"][0]["expected"] = 2.0
    with pytest.raises(SheetError) as exc:
        validate_sheet(sheet)
    assert "2.0" in str(exc.value)
    assert "canonical peak" in str(exc.value)


def test_verification_on_canonical_peak_allowed():
    # A canonical value carrying zero weight is fine -- that is how 002 works.
    sheet = _base_sheet()
    sheet["signature_locus"]["features"].append(
        {
            "id": "stripe_rhythm",
            "measure": "stripe_duty",
            "expected": 0.5,
            "tolerance": 0.01,
            "role": "verification",
            "weight": 0,
            "sample_unit": "striped_regions",
            "canonical_peaks": [0.5],
            "status": "measured",
        }
    )
    validate_sheet(sheet)


# --- identification weight sum ----------------------------------------------


def test_weight_sum_violation_rejected():
    sheet = _base_sheet()
    sheet["signature_locus"]["features"][0]["weight"] = 0.4  # sum now 0.9
    with pytest.raises(SheetError) as exc:
        validate_sheet(sheet)
    assert "sum" in str(exc.value)


def test_unmeasured_feature_counts_toward_weight_sum():
    # An unmeasured identification feature keeps its reserved weight in the sum.
    sheet = _base_sheet()
    sheet["signature_locus"]["features"][0]["weight"] = 0.5
    sheet["signature_locus"]["features"][1]["weight"] = 0.25
    sheet["signature_locus"]["features"].append(
        {
            "id": "primitive_mix",
            "measure": "primitive_frequency_mix",
            "expected": None,
            "tolerance": None,
            "role": "identification",
            "weight": 0.25,
            "sample_unit": "primitives_observed",
            "status": "unmeasured",
        }
    )
    validate_sheet(sheet)  # 0.5 + 0.25 + 0.25 == 1.0


# --- structural / schema-backed rejections ----------------------------------


def test_missing_signature_locus_rejected():
    sheet = _base_sheet()
    del sheet["signature_locus"]
    with pytest.raises(SheetError):
        validate_sheet(sheet)


def test_absolute_unit_key_rejected():
    sheet = _base_sheet()
    sheet["structure"]["band"]["module_width_px"] = 191
    with pytest.raises(SheetError) as exc:
        validate_sheet(sheet)
    assert "module_width_px" in str(exc.value)


def test_relational_feature_unknown_operand_rejected():
    sheet = _base_sheet()
    sheet["signature_locus"]["features"][1]["operands"] = ["phase_step", "nonexistent"]
    with pytest.raises(SheetError) as exc:
        validate_sheet(sheet)
    assert "nonexistent" in str(exc.value)


def test_relational_feature_without_operands_rejected():
    sheet = _base_sheet()
    del sheet["signature_locus"]["features"][1]["operands"]
    with pytest.raises(SheetError):
        validate_sheet(sheet)


def test_identification_zero_weight_rejected_by_schema():
    sheet = _base_sheet()
    sheet["signature_locus"]["features"][0]["weight"] = 0
    sheet["signature_locus"]["features"][1]["weight"] = 1.0
    with pytest.raises(SheetError):
        validate_sheet(sheet)


# --- list_sheets ------------------------------------------------------------


def test_list_sheets_returns_shipped_grammars():
    sheets = list_sheets(GRAMMARS)
    ids = {s["sheet"]["id"] for s in sheets}
    assert {"bar-cascade-001", "iso-002"} <= ids


def test_list_sheets_skips_invalid_without_crashing(tmp_path, capsys):
    # One good sheet, one broken sheet in the same directory.
    good = load_sheet(GRAMMARS / "bar-cascade-001.yaml")
    import yaml

    (tmp_path / "good.yaml").write_text(
        (GRAMMARS / "bar-cascade-001.yaml").read_text()
    )
    broken = copy.deepcopy(good)
    broken["signature_locus"]["features"][0]["expected"] = 2.0  # canonical peak
    (tmp_path / "broken.yaml").write_text(yaml.safe_dump(broken))

    result = list_sheets(tmp_path)
    ids = {s["sheet"]["id"] for s in result}
    assert "bar-cascade-001" in ids
    assert len(result) == 1  # broken one skipped
    assert "skipping invalid grammar sheet" in capsys.readouterr().err
