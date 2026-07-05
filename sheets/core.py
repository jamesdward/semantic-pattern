"""Load and validate Semantic Pattern grammar sheets.

Validation is two-stage:

1. JSON Schema (schemas/grammar-sheet/v0/grammar-sheet.schema.json) enforces
   shape, required slots and the structural role/weight constraints.
2. Semantic checks below enforce rules JSON Schema cannot express:
     a. canonical-peak rule (spec 3.7): an identification feature whose numeric
        expected sits within tolerance of a declared canonical peak is invalid.
     b. relative-units rule (spec 3.2/3.3): no _px/_mm/_pt keys in structure,
        primitive_alphabet or combination_rules.
     c. identification weights must sum to 1.0 +/- 0.001 across the locus.
     d. relational feature operands must reference known ids.

The code is deliberately plain: this implements an open spec others must
reimplement, so every check reads as its spec sentence.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

# Weight sums are compared against 1.0 with this absolute tolerance (task spec c).
WEIGHT_SUM_TOLERANCE = 0.001

# Relative-units rule: keys ending in any of these denote absolute units and are
# forbidden inside the structural slots (spec: "never absolute pixels or millimetres").
ABSOLUTE_UNIT_SUFFIXES = ("_px", "_mm", "_pt")

# Slots the relative-units sweep walks.
RELATIVE_ONLY_SLOTS = ("structure", "primitive_alphabet", "combination_rules")

_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent
    / "schemas"
    / "grammar-sheet"
    / "v0"
    / "grammar-sheet.schema.json"
)


class SheetError(Exception):
    """Raised when a grammar sheet is invalid or cannot be loaded."""


def _load_schema() -> dict:
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_sheet(path) -> dict:
    """Load a YAML grammar sheet from ``path`` and return the validated dict.

    Raises SheetError if the file is missing, is not valid YAML, is not a
    mapping, or fails validate_sheet.
    """
    path = Path(path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            sheet = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise SheetError(f"grammar sheet not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise SheetError(f"grammar sheet {path} is not valid YAML: {exc}") from exc

    if not isinstance(sheet, dict):
        raise SheetError(f"grammar sheet {path} must be a YAML mapping at the top level")

    validate_sheet(sheet)
    return sheet


def validate_sheet(sheet: dict) -> None:
    """Validate ``sheet`` against the schema and the semantic rules.

    Returns None on success; raises SheetError with an explaining message on the
    first failure.
    """
    _validate_schema(sheet)
    _check_relative_units(sheet)
    _check_canonical_peaks(sheet)
    _check_identification_weight_sum(sheet)
    _check_relational_operands(sheet)


def list_sheets(directory) -> list[dict]:
    """Return the valid grammar sheets in ``directory`` (non-recursive).

    Every *.yaml / *.yml file is attempted; invalid sheets are skipped and
    reported to stderr rather than crashing the sweep, because the recogniser
    must keep scoring against the sheets that are healthy.
    """
    import sys

    directory = Path(directory)
    valid: list[dict] = []
    for path in sorted(directory.glob("*.y*ml")):
        try:
            valid.append(load_sheet(path))
        except SheetError as exc:
            print(f"skipping invalid grammar sheet {path}: {exc}", file=sys.stderr)
    return valid


# --- semantic checks --------------------------------------------------------


def _validate_schema(sheet: dict) -> None:
    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(sheet), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        location = "/".join(str(p) for p in first.path) or "(root)"
        raise SheetError(f"schema validation failed at {location}: {first.message}")


def _iter_forbidden_unit_keys(value):
    """Yield every mapping key under ``value`` that ends in an absolute-unit suffix."""
    if isinstance(value, dict):
        for key, sub in value.items():
            if isinstance(key, str) and key.endswith(ABSOLUTE_UNIT_SUFFIXES):
                yield key
            yield from _iter_forbidden_unit_keys(sub)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_forbidden_unit_keys(item)


def _check_relative_units(sheet: dict) -> None:
    for slot in RELATIVE_ONLY_SLOTS:
        for key in _iter_forbidden_unit_keys(sheet.get(slot)):
            raise SheetError(
                f"absolute-unit key '{key}' found under '{slot}': grammar "
                f"dimensions must be relative units only (spec 3.2/3.3)"
            )


def _check_canonical_peaks(sheet: dict) -> None:
    """Canonical-peak rule (spec 3.7): identification is not allowed to rest on a
    value indistinguishable from a crowded default. An identification feature
    whose numeric expected lies within its declared tolerance of any declared
    canonical peak carries zero real signature and is rejected.
    """
    for feature in _features(sheet):
        if feature.get("role") != "identification":
            continue
        peaks = feature.get("canonical_peaks")
        if not peaks:
            continue
        expected = feature.get("expected")
        if not isinstance(expected, (int, float)) or isinstance(expected, bool):
            # Non-numeric expected (e.g. an ink pair) is compared elsewhere;
            # numeric canonical peaks do not apply to it.
            continue
        tolerance = feature.get("tolerance")
        if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool):
            # A peak is "near" only within a scalar numeric tolerance.
            continue
        for peak in peaks:
            if abs(expected - peak) <= tolerance:
                raise SheetError(
                    f"feature '{feature.get('id')}' carries identification weight "
                    f"but its expected value {expected} lies within tolerance "
                    f"{tolerance} of canonical peak {peak}: canonical peaks carry "
                    f"no signature weight (spec 3.7, exact is not distinctive)"
                )


def _check_identification_weight_sum(sheet: dict) -> None:
    """Identification weights (unmeasured features included) must sum to 1.0.

    Unmeasured-status features keep their reserved weight in the sum; the
    recogniser renormalises at runtime when it skips them.
    """
    total = sum(
        feature.get("weight", 0)
        for feature in _features(sheet)
        if feature.get("role") == "identification"
    )
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise SheetError(
            f"identification weights sum to {total}, must be 1.0 "
            f"+/- {WEIGHT_SUM_TOLERANCE} across the signature locus"
        )


def _collect_keys(value) -> set:
    """Recursively collect every mapping key under ``value`` as a set of strings."""
    found: set = set()
    if isinstance(value, dict):
        for key, sub in value.items():
            if isinstance(key, str):
                found.add(key)
            found |= _collect_keys(sub)
    elif isinstance(value, list):
        for item in value:
            found |= _collect_keys(item)
    return found


def _check_relational_operands(sheet: dict) -> None:
    """Operands of relational (measure 'relation_*') features must reference ids
    defined in combination_rules or the ids of other locus features.
    """
    features = _features(sheet)
    feature_ids = {f.get("id") for f in features if isinstance(f.get("id"), str)}
    combination_keys = _collect_keys(sheet.get("combination_rules"))
    known = feature_ids | combination_keys

    for feature in features:
        measure = feature.get("measure", "")
        if not (isinstance(measure, str) and measure.startswith("relation_")):
            continue
        for operand in feature.get("operands", []):
            if operand not in known:
                raise SheetError(
                    f"relational feature '{feature.get('id')}' references unknown "
                    f"operand '{operand}': operands must name a value in "
                    f"combination_rules or another locus feature"
                )


def _features(sheet: dict) -> list:
    locus = sheet.get("signature_locus")
    if not isinstance(locus, dict):
        return []
    features = locus.get("features")
    return features if isinstance(features, list) else []
