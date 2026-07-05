"""Grammar sheet loading and validation.

A grammar sheet is a brand's machine-readable pattern-language declaration
(spec v0.1 section 3). This package loads a YAML sheet, validates it against the
versioned JSON Schema, and then runs the semantic checks JSON Schema cannot
express -- notably the canonical-peak rule of spec section 3.7 ("exact is not
distinctive"). The recogniser scores fragments against every sheet load_sheet /
list_sheets returns, so a sheet that fails any check must never reach it.
"""

from sheets.core import SheetError, list_sheets, load_sheet, validate_sheet

__all__ = ["SheetError", "load_sheet", "validate_sheet", "list_sheets"]
