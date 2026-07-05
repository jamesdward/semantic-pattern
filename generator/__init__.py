"""Generator package: grammar sheet + params -> surfaces and fragments.

Public API:
  cascade.render / render_png / render_svg  -- bar-cascade surfaces (grammar 001)
  fragments.sample_fragment / FragmentInfo  -- ground-truthed fragment sampling
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
