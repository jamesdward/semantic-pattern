"""Generator package: grammar sheet + params -> surfaces and fragments.

Public API:
  cascade.render / render_png / render_svg  -- bar-cascade surfaces (grammar 001)
  fragments.sample_fragment / FragmentInfo  -- ground-truthed fragment sampling
  grid.render / render_with_truth / render_png  -- grid compositions (grammar iso-002)
  grid.GroundTruth  -- per-instance ground truth for grid compositions

The grid submodule is NOT eagerly imported here: it carries a __main__ CLI
(python -m generator.grid), and importing it at package init makes runpy warn
about re-execution. Reach it as ``from generator import grid`` (submodule import).
"""

from generator.cascade import render, render_png, render_svg
from generator.fragments import FragmentInfo, sample_fragment, band_boundaries_spanned

__all__ = [
    "render",
    "render_png",
    "render_svg",
    "FragmentInfo",
    "sample_fragment",
    "band_boundaries_spanned",
]
