"""
Shared presentation constants for the dashboard.

Phase C keeps styling minimal (the visual-design pass is Phase D); this module
is the single seam those changes will land in -- palette, Plotly template,
segment ordering/colours, and small formatting helpers all live here so the
view modules never hard-code a colour.
"""

from __future__ import annotations

PLOTLY_TEMPLATE = "plotly_white"

# Segment vocabulary (must match analytics/segmentation.SEGMENT_LABELS order).
SEGMENT_ORDER = ["Champion", "Loyal", "New", "At Risk", "Hibernating", "Needs Attention"]
SEGMENT_COLORS = {
    "Champion": "#2E7D32",
    "Loyal": "#66BB6A",
    "New": "#42A5F5",
    "At Risk": "#EF6C00",
    "Hibernating": "#9E9E9E",
    "Needs Attention": "#C62828",
}

# RFM x CLV quadrant labels + colours (Phase C combined view).
QUADRANT_ORDER = [
    "Protect (high RFM, high CLV)",
    "Win back (low RFM, high CLV)",
    "Upsell (high RFM, low CLV)",
    "Low priority (low RFM, low CLV)",
]
QUADRANT_COLORS = {
    QUADRANT_ORDER[0]: "#2E7D32",
    QUADRANT_ORDER[1]: "#EF6C00",
    QUADRANT_ORDER[2]: "#42A5F5",
    QUADRANT_ORDER[3]: "#9E9E9E",
}

SEQUENTIAL_SCALE = "Blues"  # cohort heatmap

FORECAST_COLORS = {
    "actual": "#90A4AE",
    "linreg-v1": "#1565C0",
    "holtwinters-v1": "#C62828",
}


def money(x: float) -> str:
    return f"${x:,.0f}"


def pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"
