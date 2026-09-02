"""
The single styling seam for the dashboard (Phase D visual design pass).

Everything visual is a **named token** here — surfaces, borders, the text
tiers, the one accent, the muted semantic colours, the Plotly template, and
the CSS. No view module hard-codes a colour or a font; they import from this
file. `.streamlit/config.toml` mirrors the framework-level values so Streamlit's
own chrome matches rather than fights the injected CSS.

Aesthetic target: a modern dark-mode developer-console dashboard — a layered
near-black foundation (page → elevated card → inset), depth from three surface
tiers plus 1px borders (not drop shadows), one warm accent used sparingly, and
a clear typographic scale in Inter.

Accent choice — warm gold `#E8B23A`. Retail analytics is about revenue and
customer value, and gold is the universal commercial signal (price, premium,
loyalty tiers). A warm accent against cool-neutral dark surfaces is what
creates the sense of depth, and it is deliberately not Neon's green so the
result reads as Vendrite's own. It is reserved for: the active nav item, the
primary button, a single highlighted value, and the "current"/emphasis line in
a chart — never a regular data series. The one risk (gold vs. an amber warning
state) is handled the data-viz way: status colours always carry an icon + word,
never colour alone.

The categorical chart palette is the data-viz skill's validated dark colorway
(cool-neutral), kept unchanged — its slot order is the CVD-safety mechanism and
was re-validated against this surface (`#161618`, all checks pass). Data colours
and the UI accent are intentionally separate channels; the gold identity
carries through charts only at genuine highlight points.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# ===========================================================================
# tokens
# ===========================================================================
# ---- surfaces: three-tier dark foundation --------------------------------
BG_BASE = "#0B0B0C"          # page background (near-black, faint cool cast)
BG_ELEVATED = "#161618"      # card / panel / chart surface
BG_INSET = "#1F1F22"         # nested: table header, inputs, hovered nav row
BORDER = "#282829"           # 1px card edges
BORDER_STRONG = "#37373A"    # hover / focus ring

# ---- text tiers (never pure white on pure black) -----------------------
TEXT_PRIMARY = "#EDEDED"     # headings, KPI values          (15.4:1 on card)
TEXT_SECONDARY = "#A1A1A6"   # labels, body, secondary text  (7.0:1)
TEXT_MUTED = "#78787F"       # captions, metadata            (3.9:1)

# ---- accent: warm gold (see module docstring for the reasoning) --------
ACCENT = "#E8B23A"
ACCENT_HOVER = "#F2C25C"
ACCENT_QUIET = "#2A2213"     # accent-tinted background for the active nav item
ACCENT_INK = "#0B0B0C"       # text/icon colour to sit ON the accent (10.2:1)

# ---- semantic: muted + desaturated, always shipped with an icon+word --
OK = "#5F9E6A"
WARN = "#B98A3C"
ERROR = "#C7625C"

# ---- chart chrome ----------------------------------------------------------
GRID = "#212124"             # hairline gridline, barely off the surface
AXIS_LINE = BORDER

FONT_STACK = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"

# ---- icons -----------------------------------------------------------------
# ONE icon set everywhere: Material Symbols Rounded. Two delivery paths, same
# font — `icon()` / `section()` emit a <span> against the @import'ed face, and
# Streamlit widget `icon=` params take the ":material/name:" shortcode (which
# Streamlit renders with the same family). Icons are wayfinding, so they wear
# TEXT_MUTED / TEXT_SECONDARY — never the accent, which is reserved for things
# that actually need attention.
ICON_SM = "16px"
ICON_MD = "18px"

# ===========================================================================
# chart palettes
# ===========================================================================
# Data-viz skill's validated dark categorical colorway — DO NOT reorder (the
# order is the colour-vision-deficiency safety mechanism). Re-validated on the
# #161618 surface: all checks pass, worst adjacent CVD ΔE 8.4.
CATEGORICAL = [
    "#3987e5",  # blue
    "#d95926",  # burnt orange
    "#199e70",  # deep aqua
    "#c98500",  # amber
    "#d55181",  # magenta
    "#008300",  # green
    "#9085e9",  # violet
    "#e66767",  # red
]

# Segment vocabulary (order must match analytics/segmentation.SEGMENT_LABELS).
# Kept semantic — a Champion→Hibernating health gradient — restated in muted
# dark-theme tones. Always rendered with a legend / labels, never colour-alone.
SEGMENT_ORDER = ["Champion", "Loyal", "New", "At Risk", "Hibernating", "Needs Attention"]
SEGMENT_COLORS = {
    "Champion": "#4E9E5F",        # muted green — best
    "Loyal": "#6FB98A",           # lighter muted green
    "New": "#3987e5",             # blue — fresh
    "At Risk": "#B98A3C",         # muted amber — caution
    "Hibernating": "#6B6B70",     # muted grey — dormant
    "Needs Attention": "#C7625C", # muted red — worst
}

# RFM × CLV quadrants. On the scatter these are shown by position (median
# crosshair) + corner labels, not a colour axis — four categorical hues cannot
# clear the scatter all-pairs CVD gate. These colours are only the swatches in
# the quadrant summary table.
QUADRANT_ORDER = [
    "Protect (high RFM, high CLV)",
    "Win back (low RFM, high CLV)",
    "Upsell (high RFM, low CLV)",
    "Low priority (low RFM, low CLV)",
]
QUADRANT_COLORS = {
    QUADRANT_ORDER[0]: OK,          # protect — good
    QUADRANT_ORDER[1]: WARN,        # win back — caution
    QUADRANT_ORDER[2]: "#3987e5",   # upsell — neutral blue
    QUADRANT_ORDER[3]: "#6B6B70",   # low priority — de-emphasised grey
}

# Sequential single hue for the cohort heatmap: an amber ramp (ties the heat to
# the accent hue), dark→light, lightness-monotonic. Brighter = higher retention.
SEQUENTIAL_AMBER = [
    "#171207", "#332810", "#54401a", "#7c5f26",
    "#a67f38", "#cf9f4d", "#e6c37e", "#f3ddad",
]
SEQUENTIAL_SCALE = SEQUENTIAL_AMBER  # px.imshow / color_continuous_scale accept a hex list


def sequential_colorscale() -> list[list]:
    """The amber ramp as explicit [position, colour] pairs — what
    ``go.Heatmap`` (unlike Plotly Express) requires."""
    last = len(SEQUENTIAL_AMBER) - 1
    return [[i / last, hx] for i, hx in enumerate(SEQUENTIAL_AMBER)]

# Forecast chart: neutral actual + one hue per model (blue↔magenta, CVD ΔE 15.9).
FORECAST_COLORS = {
    "actual": "#8A8A90",
    "linreg-v1": "#3987e5",
    "holtwinters-v1": "#d55181",
}


# ===========================================================================
# Plotly template
# ===========================================================================
def _axis(show_grid: bool) -> dict:
    return dict(
        showgrid=show_grid,
        gridcolor=GRID,
        gridwidth=1,
        zeroline=False,
        showline=True,
        linecolor=AXIS_LINE,
        linewidth=1,
        ticks="",
        tickfont=dict(color=TEXT_MUTED, size=11, family=FONT_STACK),
        title=dict(font=dict(color=TEXT_SECONDARY, size=12, family=FONT_STACK)),
        automargin=True,
    )


def _template() -> go.layout.Template:
    ramp = [[i / (len(SEQUENTIAL_AMBER) - 1), hx] for i, hx in enumerate(SEQUENTIAL_AMBER)]
    return go.layout.Template(
        layout=dict(
            paper_bgcolor=BG_ELEVATED,
            plot_bgcolor=BG_ELEVATED,
            font=dict(family=FONT_STACK, size=12, color=TEXT_SECONDARY),
            title=dict(font=dict(family=FONT_STACK, size=15, color=TEXT_PRIMARY), x=0),
            colorway=CATEGORICAL,
            xaxis=_axis(show_grid=False),
            yaxis=_axis(show_grid=True),
            legend=dict(
                bgcolor="rgba(0,0,0,0)",
                bordercolor="rgba(0,0,0,0)",
                font=dict(family=FONT_STACK, color=TEXT_SECONDARY, size=11),
            ),
            colorscale=dict(sequential=ramp),
            coloraxis=dict(colorbar=dict(
                outlinewidth=0,
                tickfont=dict(color=TEXT_MUTED, size=11, family=FONT_STACK),
                title=dict(font=dict(color=TEXT_SECONDARY, size=12, family=FONT_STACK)),
            )),
            hoverlabel=dict(
                bgcolor=BG_INSET,
                bordercolor=BORDER,
                font=dict(family=FONT_STACK, color=TEXT_PRIMARY, size=12),
            ),
            margin=dict(l=8, r=8, t=30, b=8),
        )
    )


PLOTLY_TEMPLATE = "vendrite_dark"
pio.templates[PLOTLY_TEMPLATE] = _template()
pio.templates.default = PLOTLY_TEMPLATE


# ===========================================================================
# formatting helpers
# ===========================================================================
def money(x: float) -> str:
    return f"${x:,.0f}"


def pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


# ===========================================================================
# CSS — one injection, called once from app.py after set_page_config
# ===========================================================================
_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,400,0,0&display=swap');

:root {{
  --bg-base: {BG_BASE};
  --bg-elevated: {BG_ELEVATED};
  --bg-inset: {BG_INSET};
  --border: {BORDER};
  --border-strong: {BORDER_STRONG};
  --text-primary: {TEXT_PRIMARY};
  --text-secondary: {TEXT_SECONDARY};
  --text-muted: {TEXT_MUTED};
  --accent: {ACCENT};
  --accent-hover: {ACCENT_HOVER};
  --accent-quiet: {ACCENT_QUIET};
  --accent-ink: {ACCENT_INK};
  --ok: {OK};
  --error: {ERROR};
  --radius: 10px;
  --icon-sm: {ICON_SM};
  --icon-md: {ICON_MD};
}}

/* ---- icons (Material Symbols Rounded) ------------------------------- */
.vd-icon {{
  font-family: 'Material Symbols Rounded';
  font-weight: 400; font-style: normal; line-height: 1;
  font-size: var(--icon-sm); vertical-align: -3px;
  color: var(--text-muted); user-select: none;
  font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 20;
}}
.vd-icon.md {{ font-size: var(--icon-md); vertical-align: -4px; }}

/* ---- section header with icon --------------------------------------- */
.vd-section {{
  display: flex; align-items: center; gap: .45rem;
  font-size: .95rem; font-weight: 600; color: var(--text-primary);
  letter-spacing: -0.01em; margin: 0 0 .55rem;
}}
.vd-section .vd-icon {{ color: var(--text-secondary); }}

/* ---- KPI card (hand-built so the delta can use muted semantics) ----- */
.vd-kpi {{
  background: var(--bg-elevated); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 15px 17px 14px; height: 100%;
}}
.vd-kpi-label {{
  display: flex; align-items: center; gap: .4rem;
  font-size: .7rem; font-weight: 600; letter-spacing: .08em;
  text-transform: uppercase; color: var(--text-muted); margin-bottom: .45rem;
}}
.vd-kpi-value {{
  font-size: 1.85rem; font-weight: 650; color: var(--text-primary);
  line-height: 1.15; letter-spacing: -0.02em;
}}
.vd-kpi-delta {{
  display: flex; align-items: center; gap: .2rem;
  font-size: .76rem; font-weight: 500; margin-top: .3rem;
  color: var(--text-muted); font-variant-numeric: tabular-nums;
}}
.vd-kpi-delta .vd-icon {{ font-size: 14px; vertical-align: -2px; }}
.vd-kpi-delta.up, .vd-kpi-delta.up .vd-icon {{ color: var(--ok); }}
.vd-kpi-delta.down, .vd-kpi-delta.down .vd-icon {{ color: var(--error); }}

/* ---- base type ------------------------------------------------------- */
/* Do NOT add a broad [class*="st-"] selector here. Streamlit's own icon
   spans carry st-emotion-cache-* classes, and this <style> is injected
   after Streamlit's stylesheet, so a blanket [class*="st-"] font-family
   beats (same specificity, later source order) the emotion rule that makes
   Material Symbols ligatures render — the icon *name* then shows as raw
   text (sidebar collapse, expander chevrons, the password show/hide eye).
   font-family inherits, so setting it on the app root is enough. */
html, body, .stApp, .stMarkdown, button, input, textarea, select {{
  font-family: {FONT_STACK};
}}
.stApp {{ background: var(--bg-base); }}

/* Re-assert the ligature icon font on Streamlit's own icon elements, so no
   rule here (nor a future one) can degrade native chrome icons — sidebar
   collapse, expander chevrons, password show/hide, widget `icon=` glyphs —
   to their literal text names. Streamlit bundles this face locally, so this
   does not depend on the Google Fonts @import above. */
span[data-testid="stIconMaterial"],
span.material-icons, span.material-icons-outlined,
span.material-icons-rounded, span.material-icons-sharp {{
  font-family: 'Material Symbols Rounded' !important;
  font-weight: normal !important;
  font-style: normal !important;
  letter-spacing: normal !important;
  text-transform: none !important;
}}
.block-container {{ padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1400px; }}

h1, h2, h3 {{ color: var(--text-primary); letter-spacing: -0.015em; line-height: 1.3; }}
h1 {{ font-size: 1.75rem; font-weight: 600; margin-bottom: .1rem; }}
h2 {{ font-size: 1.15rem; font-weight: 600; }}
h3 {{ font-size: .95rem; font-weight: 600; }}
[data-testid="stCaptionContainer"], .stCaption, small {{
  color: var(--text-muted); font-size: .8rem; line-height: 1.5;
}}
p, li, .stMarkdown {{ color: var(--text-secondary); line-height: 1.55; }}
hr {{ border-color: var(--border); margin: 1.1rem 0; }}
a {{ color: var(--accent); }}

/* ---- KPI cards (st.metric styled as a card) ------------------------- */
[data-testid="stMetric"] {{
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 18px;
}}
[data-testid="stMetricLabel"] p {{
  font-size: .7rem; font-weight: 600; letter-spacing: .08em;
  text-transform: uppercase; color: var(--text-muted);
}}
[data-testid="stMetricValue"] {{
  font-size: 1.9rem; font-weight: 650; color: var(--text-primary);
  font-variant-numeric: normal;
}}
[data-testid="stMetricDelta"] {{ font-size: .78rem; font-weight: 500; }}

/* ---- generic bordered container = card ----------------------------- */
[data-testid="stVerticalBlockBorderWrapper"] {{
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
}}

/* ---- expander --------------------------------------------------------- */
[data-testid="stExpander"] details {{
  background: var(--bg-elevated); border: 1px solid var(--border);
  border-radius: var(--radius);
}}
[data-testid="stExpander"] summary {{ color: var(--text-secondary); }}

/* ---- sidebar: product nav ----------------------------------------- */
[data-testid="stSidebar"] {{ background: var(--bg-base); border-right: 1px solid var(--border); }}
[data-testid="stSidebar"] .block-container {{ padding-top: 1.4rem; }}
[data-testid="stSidebarNav"] {{ padding-top: .3rem; }}
[data-testid="stSidebarNav"] > div > div {{ /* section header */
  font-size: .68rem; font-weight: 600; letter-spacing: .09em;
  text-transform: uppercase; color: var(--text-muted);
  padding: .5rem .25rem .25rem;
}}
[data-testid="stSidebarNav"] a {{
  border-radius: 8px; color: var(--text-secondary);
  padding: .4rem .55rem; transition: background .12s ease, color .12s ease;
}}
[data-testid="stSidebarNav"] a:hover {{ background: var(--bg-inset); color: var(--text-primary); }}
[data-testid="stSidebarNav"] a[aria-current="page"] {{
  background: var(--accent-quiet);
  color: var(--text-primary);
  box-shadow: inset 2px 0 0 0 var(--accent);
}}
[data-testid="stSidebarNav"] a[aria-current="page"] span {{ color: var(--text-primary); }}

/* ---- sidebar filters: keep multiselect chips inside the rail ------- */
/* BaseWeb's tag container can lay chips out on one non-wrapping row, which
   overflows the ~300px sidebar and drags the layout sideways. Force wrap and
   cap each chip so long category names ellipsize instead of pushing width. */
[data-testid="stSidebar"] [data-testid="stMultiSelect"] div[data-baseweb="select"] > div:first-child {{
  flex-wrap: wrap; overflow: hidden;
}}
[data-testid="stSidebar"] [data-testid="stMultiSelect"] span[data-baseweb="tag"] {{
  max-width: 100%;
}}
[data-testid="stSidebar"] [data-testid="stMultiSelect"] span[data-baseweb="tag"] span {{
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}}

/* ---- buttons ------------------------------------------------------- */
.stButton > button, .stDownloadButton > button {{
  border-radius: 8px; border: 1px solid var(--border);
  background: var(--bg-inset); color: var(--text-secondary);
  font-weight: 500; transition: border-color .12s ease, color .12s ease;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
  border-color: var(--border-strong); color: var(--text-primary);
}}
.stButton > button[kind="primary"], .stButton > button[data-testid="stBaseButton-primary"] {{
  background: var(--accent); border-color: var(--accent); color: var(--accent-ink);
}}
.stButton > button[kind="primary"]:hover {{ background: var(--accent-hover); border-color: var(--accent-hover); }}

/* ---- inputs ------------------------------------------------------- */
[data-baseweb="input"], [data-baseweb="select"] > div, .stTextInput input,
.stDateInput input, [data-baseweb="popover"] {{
  background: var(--bg-inset) !important; border-color: var(--border) !important;
}}
.stTextInput input, .stDateInput input {{ color: var(--text-primary); }}

/* ---- dataframes / tables --------------------------------------------- */
[data-testid="stDataFrame"] {{ border: 1px solid var(--border); border-radius: var(--radius); }}
[data-testid="stTable"] table {{ border: none; }}
[data-testid="stTable"] thead th, [data-testid="stTable"] tbody th {{
  background: var(--bg-inset); color: var(--text-muted);
  font-size: .68rem; font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
  border-color: var(--border); text-align: left;
}}
[data-testid="stTable"] tbody td {{
  color: var(--text-secondary); border-color: var(--border);
  font-variant-numeric: tabular-nums; text-align: right;
}}
[data-testid="stTable"] tbody tr:hover td, [data-testid="stTable"] tbody tr:hover th {{
  background: var(--bg-inset);
}}

/* ---- login form as a card --------------------------------------------- */
[data-testid="stForm"] {{
  background: var(--bg-elevated); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 22px 22px 8px;
}}
[data-testid="stForm"] [data-testid="stFormSubmitButton"] button {{
  width: 100%; background: var(--accent); border-color: var(--accent);
  font-weight: 600;
}}
[data-testid="stForm"] [data-testid="stFormSubmitButton"] button:hover {{
  background: var(--accent-hover); border-color: var(--accent-hover);
}}
/* the label is a nested <p>, which the base `p {{ color: ... }}` rule paints
   text-secondary — grey on gold reads as washed out (~1.9:1). Force the
   dark ink (10.2:1) on the button and its markdown descendants. Applies to
   every primary/submit button, not just login. */
[data-testid="stForm"] [data-testid="stFormSubmitButton"] button,
[data-testid="stForm"] [data-testid="stFormSubmitButton"] button p,
.stButton > button[data-testid="stBaseButton-primary"],
.stButton > button[data-testid="stBaseButton-primary"] p {{
  color: var(--accent-ink) !important;
}}
/* both login inputs the same width regardless of the password reveal icon */
[data-testid="stForm"] [data-baseweb="input"] {{ width: 100%; }}

/* Streamlit's "Press Enter to submit form" / "Press Enter to apply" hint is
   positioned absolutely inside the input's end slot, where it overlaps the
   field. Enter still works without it — drop it for a clean surface. */
[data-testid="InputInstructions"] {{ display: none !important; }}

/* ---- radio / segmented controls as quiet pills --------------------- */
[data-testid="stRadio"] label {{ color: var(--text-secondary); }}

/* ---- Vendrite wordmark (sidebar + login) --------------------------- */
.vd-wordmark {{
  font-weight: 650; font-size: 1.05rem; letter-spacing: -0.01em;
  color: var(--text-primary); display: flex; align-items: center; gap: .5rem;
  padding: .1rem .25rem .2rem;
}}
.vd-wordmark::before {{ content: "\\25C6"; color: var(--accent); font-size: .9rem; }}
.vd-wordmark small {{
  color: var(--text-muted); font-weight: 500; font-size: .72rem;
  letter-spacing: .04em; text-transform: uppercase;
}}

/* ---- login card --------------------------------------------------- */
.vd-login-head {{ text-align: center; margin: 2.5rem 0 .35rem; }}
.vd-login-head .vd-wordmark {{ justify-content: center; font-size: 1.5rem; }}
.vd-login-head .vd-wordmark::before {{ font-size: 1.2rem; }}
.vd-login-sub {{ text-align: center; color: var(--text-muted); font-size: .82rem; margin-bottom: 1.2rem; }}
</style>
"""


def inject_css() -> None:
    """Inject the dashboard's stylesheet. Call once, right after
    ``st.set_page_config`` in the entry script."""
    st.markdown(_CSS, unsafe_allow_html=True)


# ===========================================================================
# presentation mode — hide the dev-tool chrome that floats over charts
# ===========================================================================
# These are all Streamlit-internal test ids — the fragile part of the
# stylesheet. A future Streamlit release could rename any of them; if a piece
# of chrome reappears after an upgrade, the matching line here is what to
# update. Everything outside this block targets our own markup.
#
#   stElementToolbar   per-element hover toolbar (fullscreen / download)
#   stToolbar          top-right app toolbar — on Community Cloud this is
#                      where the host injects Share / GitHub / edit for the
#                      app owner (a plain visitor never sees those)
#   stStatusWidget     bottom-right "Manage app" / running-status pill
#   stMainMenu         the ⋮ hamburger menu
#   stAppDeployButton  the "Deploy" button shown to the owner in local/dev
#
# What this CANNOT reach: the slide-up "Manage app" console itself, the
# Streamlit Cloud account bar, and the *.streamlit.app browser chrome — those
# live in the host page, outside the app iframe. With stStatusWidget hidden a
# viewer has no button to open that console; an owner viewing their own app
# may still get a Cloud-level affordance regardless (accept it, or view
# logged-out / incognito for the clean surface).
_TOOLBAR_SELECTOR = '[data-testid="stElementToolbar"]'
_CHROME_SELECTORS = ", ".join([
    _TOOLBAR_SELECTOR,
    '[data-testid="stToolbar"]',
    '[data-testid="stStatusWidget"]',
    '[data-testid="stMainMenu"]',
    '[data-testid="stAppDeployButton"]',
])

_PRESENTATION_CSS = f"""
<style>
{_CHROME_SELECTORS} {{ display: none !important; }}
</style>
"""


def presentation_mode() -> bool:
    """True when the sidebar's Presentation-mode toggle is on (the default)."""
    return bool(st.session_state.get("presentation_mode", True))


def inject_mode_css(presentation: bool) -> None:
    """Inject the presentation-mode-dependent CSS. Call once per run, from the
    entry script, after the toggle has been rendered."""
    if presentation:
        st.markdown(_PRESENTATION_CSS, unsafe_allow_html=True)


def plotly_config() -> dict:
    """Config for every ``st.plotly_chart`` — hides Plotly's floating modebar in
    presentation mode, keeps the full zoom/pan/download toolbar when it is off."""
    show = not presentation_mode()
    return {"displayModeBar": show, "displaylogo": False, "scrollZoom": False}


# ===========================================================================
# small render helpers (the only place icon/KPI markup is written)
# ===========================================================================
def icon(name: str, *, size_md: bool = False) -> str:
    """Inline Material Symbols glyph as an HTML string."""
    return f'<span class="vd-icon{" md" if size_md else ""}">{name}</span>'


def section(title: str, icon_name: str) -> None:
    """A section header with a leading muted icon."""
    st.markdown(
        f'<div class="vd-section">{icon(icon_name, size_md=True)}{title}</div>',
        unsafe_allow_html=True,
    )


def kpi_card(container, *, label: str, icon_name: str, value: str,
             delta: str | None = None, direction: int = 0) -> None:
    """Render one KPI card into ``container``.

    ``direction``: +1 up / -1 down / 0 neutral. The arrow is always paired with
    the number — colour never carries the meaning on its own.
    """
    if delta is None:
        delta_html = ""
    else:
        cls = "up" if direction > 0 else "down" if direction < 0 else ""
        arrow = ""
        if direction > 0:
            arrow = icon("arrow_upward")
        elif direction < 0:
            arrow = icon("arrow_downward")
        delta_html = f'<div class="vd-kpi-delta {cls}">{arrow}{delta}</div>'
    container.markdown(
        f'<div class="vd-kpi">'
        f'<div class="vd-kpi-label">{icon(icon_name)}{label}</div>'
        f'<div class="vd-kpi-value">{value}</div>'
        f"{delta_html}</div>",
        unsafe_allow_html=True,
    )
