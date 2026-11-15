"""Tax calculation engines."""

from .marginal import (
    ANNOTATIONS,
    LAYER_LABELS,
    LAYERS,
    Annotation,
    MarginalSeries,
    build_marginal_series,
    default_visibility,
)
from .tax import IncomeSource, TaxResult, compute_tax, senior_deduction, taxable_social_security

__all__ = [
    "ANNOTATIONS", "LAYER_LABELS", "LAYERS", "Annotation", "MarginalSeries",
    "build_marginal_series", "default_visibility", "IncomeSource", "TaxResult",
    "compute_tax", "senior_deduction", "taxable_social_security",
]
