"""
make_map.py
-----------
Interactive map of SW Pennsylvania legacy industrial sites for the
AI-enabled manufacturing revitalization proposal.

DATA PRIORITY (what's actually actionable for the proposal):
  HIGH  — WPRDC vacant/underutilized industrial properties (idle buildings)
  HIGH  — EPA CIMC sites with remediated brownfield status (cleared for reuse)
  MED   — EPA CIMC sites under assessment (contamination risk, not ready)
  LOW   — Abandoned mine sites (context only — mines ≠ buildings, not targets)

VISUALIZATION:
  • Heatmap   — density overview of all industrial activity
  • Bubbles   — WPRDC industrial properties sized by lot area, colored by vacancy
  • Dots      — EPA CIMC sites colored by remediation status
  • Mines     — separate heatmap, off by default (context layer)
  • Labels    — Mon Valley community names

Output:  analysis/swpa_industrial_map.html
Run:     python make_map.py
"""

import logging
from pathlib import Path

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
from branca.colormap import linear
from folium.plugins import HeatMap, Fullscreen, MiniMap

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_FILE = Path(__file__).parent / "swpa_industrial_map.html"

MAP_CENTER = [40.30, -79.95]
MAP_ZOOM = 10


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def load(name: str) -> gpd.GeoDataFrame | None:
    path = DATA_DIR / f"{name}.geojson"
    if not path.exists():
        log.warning("Missing %s — run fetch_data.py first", path.name)
        return None
    gdf = gpd.read_file(path)
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    log.info("Loaded %s: %d rows", name, len(gdf))
    return gdf


def centroid(geom):
    if geom is None or geom.is_empty:
        return None
    pt = geom.centroid if geom.geom_type != "Point" else geom
    return (pt.y, pt.x)


def popup(row: pd.Series, fields: list[str], header: str = "", color: str = "#333") -> folium.Popup:
    lines = []
    if header:
        lines.append(f"<b style='color:{color}'>{header}</b>")
    for f in fields:
        val = row.get(f)
        if val and str(val).strip() not in ("", "None", "nan"):
            lines.append(f"<b>{f.replace('_',' ').title()}:</b> {val}")
    html = "<br>".join(lines) or "No data"
    return folium.Popup(folium.IFrame(html, width=280, height=200), max_width=300)


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1: Combined heatmap (density overview)
# NOTE: HeatMap must be added to the map directly, not inside FeatureGroup,
# to avoid a known folium rendering bug. We use a workaround with a named group.
# ─────────────────────────────────────────────────────────────────────────────

def add_heatmap(m: folium.Map, *gdfs: gpd.GeoDataFrame | None):
    points = []
    weights = [1.0, 0.7, 0.5, 0.4]  # higher weight = more relevant to proposal
    for gdf, w in zip(gdfs, weights):
        if gdf is None or len(gdf) == 0:
            continue
        for geom in gdf.geometry:
            c = centroid(geom)
            if c:
                points.append([c[0], c[1], w])

    if not points:
        log.warning("Heatmap: no points to show.")
        return

    # Add HeatMap directly to map (not FeatureGroup) — avoids folium rendering bug
    HeatMap(
        points,
        name="Density Heatmap — All Industrial Sites",
        min_opacity=0.3,
        max_opacity=0.8,
        radius=20,
        blur=22,
        gradient={0.2: "#313695", 0.45: "#74add1", 0.6: "#fee090",
                  0.75: "#f46d43", 0.9: "#d73027", 1.0: "#a50026"},
        show=True,
    ).add_to(m)
    log.info("Heatmap: %d points", len(points))


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2: WPRDC industrial properties — bubble map (PRIMARY layer)
# ─────────────────────────────────────────────────────────────────────────────

def _vacancy_class(row: pd.Series) -> str:
    desc = (str(row.get("USEDESC","")) + " " + str(row.get("CLASSDESC",""))).upper()
    if "VACANT" in desc:
        return "vacant"
    land  = pd.to_numeric(row.get("FAIRMARKETLAND",  0), errors="coerce") or 0
    total = pd.to_numeric(row.get("FAIRMARKETTOTAL", 0), errors="coerce") or 0
    bldg  = pd.to_numeric(row.get("FAIRMARKETBUILDING", 0), errors="coerce") or 0
    # Building value near zero relative to total → structure likely derelict/absent
    if total > 0 and bldg / total < 0.10:
        return "likely_vacant"
    return "active"


VACANCY = {
    "vacant":        {"color": "#c62828", "fill": "#ef5350", "label": "Vacant Industrial"},
    "likely_vacant": {"color": "#bf360c", "fill": "#ff7043", "label": "Likely Underutilized Industrial"},
    "active":        {"color": "#1b5e20", "fill": "#66bb6a", "label": "Active Industrial"},
}

def add_wprdc_parcels(m: folium.Map, gdf: gpd.GeoDataFrame):
    """
    WPRDC data now has polygon geometry (joined from Allegheny County parcels).
    Render as filled polygons colored by vacancy status.
    Use circle markers at centroid for bubble-map feel — polygons are often
    too small to see at county zoom, and 3400 polygons would be very slow.
    """
    log.info("Adding WPRDC industrial property markers…")
    gdf = gdf.copy()
    gdf["_class"] = gdf.apply(_vacancy_class, axis=1)
    gdf["_area"]  = pd.to_numeric(gdf.get("LOTAREA", 0), errors="coerce").fillna(0)
    cap = gdf["_area"].quantile(0.95) or 1
    gdf["_r"] = (gdf["_area"].clip(upper=cap) / cap * 14 + 5).fillna(5)

    popup_fields = ["PROPERTYADDRESS","PROPERTYCITY","USEDESC","CLASSDESC",
                    "MUNIDESC","LOTAREA","YEARBLT","CONDITION",
                    "FAIRMARKETBUILDING","FAIRMARKETLAND","FAIRMARKETTOTAL"]

    for cls, style in VACANCY.items():
        sub = gdf[gdf["_class"] == cls]
        layer = folium.FeatureGroup(
            name=f"WPRDC: {style['label']} ({len(sub)})",
            show=(cls != "active"),
        )
        for _, row in sub.iterrows():
            c = centroid(row.geometry)
            if not c:
                continue
            folium.CircleMarker(
                location=c,
                radius=float(row["_r"]),
                color=style["color"],
                fill=True, fill_color=style["fill"], fill_opacity=0.7,
                weight=1,
                popup=popup(row, popup_fields, style["label"], style["color"]),
                tooltip=f"{row.get('PROPERTYADDRESS','?')} — {style['label']}",
            ).add_to(layer)
        layer.add_to(m)
        log.info("  WPRDC %s: %d", cls, len(sub))


# ─────────────────────────────────────────────────────────────────────────────
# Layer 3: Opportunity Score — composite 0–100 ranking per parcel
#
# Component scores (all normalized 0–1, then weighted):
#
#   Vacancy status      (35%) — vacant=1.0, likely_vacant=0.6, active=0.0
#   Lot area            (25%) — percentile rank; bigger sites score higher
#   Building dereliction(20%) — land value / total value; higher = worse structure
#   Acquisition cost    (15%) — inverted fair market total percentile; cheaper = higher
#   Building age        ( 5%) — older = more legacy industrial; pre-1960 scores highest
#
# Score is only computed for vacant + likely_vacant parcels. Active industrial
# sites are excluded — they're already in use and not available for the proposal.
# ─────────────────────────────────────────────────────────────────────────────

# Colormap: green (low opportunity) → yellow → red (high opportunity)
SCORE_COLORMAP = linear.RdYlGn_11.scale(0, 100)
SCORE_COLORMAP.caption = "Opportunity Score (0 = low · 100 = highest redevelopment priority)"


def compute_opportunity_scores(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    gdf = gdf.copy()

    # Pre-classify vacancy
    gdf["_class"] = gdf.apply(_vacancy_class, axis=1)

    # Only score non-active parcels (those actually available for the proposal)
    mask = gdf["_class"] != "active"
    scored = gdf[mask].copy()

    def to_num(col, default=0):
        return pd.to_numeric(scored.get(col, default), errors="coerce").fillna(default)

    def pct_rank(series: pd.Series) -> pd.Series:
        """Percentile rank normalized 0–1."""
        return series.rank(pct=True)

    # ── component 1: vacancy (35 pts) ────────────────────────────────────────
    vacancy_map = {"vacant": 1.0, "likely_vacant": 0.6}
    scored["_s_vacancy"] = scored["_class"].map(vacancy_map).fillna(0.0) * 35

    # ── component 2: lot area percentile (25 pts) ────────────────────────────
    scored["_s_area"] = pct_rank(to_num("LOTAREA")) * 25

    # ── component 3: building dereliction — land/total ratio (20 pts) ────────
    land  = to_num("FAIRMARKETLAND")
    total = to_num("FAIRMARKETTOTAL")
    # Avoid division by zero; ratio = 1.0 when total is 0 (nothing assessed)
    ratio = (land / total.replace(0, np.nan)).fillna(1.0).clip(0, 1)
    scored["_s_derelict"] = ratio * 20

    # ── component 4: acquisition affordability (15 pts) ──────────────────────
    # Lower fair market total = easier/cheaper to acquire → invert the rank
    scored["_s_afford"] = (1 - pct_rank(to_num("FAIRMARKETTOTAL"))) * 15

    # ── component 5: building age — pre-1960 legacy industrial (5 pts) ───────
    year = to_num("YEARBLT", default=9999)
    current_year = 2026
    # Age score: built before 1960 → 1.0, built after 2000 → 0.0, linear between
    age_score = ((current_year - year - 26) / (current_year - 1960 - 26)).clip(0, 1)
    age_score[year == 9999] = 0.0   # unknown year → no bonus
    scored["_s_age"] = age_score * 5

    # ── total score ──────────────────────────────────────────────────────────
    scored["opportunity_score"] = (
        scored["_s_vacancy"] + scored["_s_area"] +
        scored["_s_derelict"] + scored["_s_afford"] + scored["_s_age"]
    ).round(1)

    log.info("Opportunity scores: min=%.1f  median=%.1f  max=%.1f",
             scored["opportunity_score"].min(),
             scored["opportunity_score"].median(),
             scored["opportunity_score"].max())
    return scored


def _score_color(score: float) -> str:
    """Red = high opportunity, green = low — inverted from SCORE_COLORMAP direction."""
    # RdYlGn goes green→red as value increases; we want red=high, so invert
    return SCORE_COLORMAP(100 - score)


def add_opportunity_layer(m: folium.Map, gdf: gpd.GeoDataFrame):
    log.info("Adding opportunity score layer…")
    scored = compute_opportunity_scores(gdf)

    # Size bubbles by lot area (same as vacancy layer)
    scored["_area"] = pd.to_numeric(scored.get("LOTAREA", 0), errors="coerce").fillna(0)
    cap = scored["_area"].quantile(0.95) or 1
    scored["_r"] = (scored["_area"].clip(upper=cap) / cap * 14 + 5).fillna(5)

    # Split into three tiers for the layer control so users can focus on top sites
    tiers = [
        ("High",   scored["opportunity_score"] >= 70, True),
        ("Medium", scored["opportunity_score"].between(45, 70), False),
        ("Low",    scored["opportunity_score"] < 45,  False),
    ]

    popup_fields = ["PROPERTYADDRESS", "PROPERTYCITY", "USEDESC",
                    "MUNIDESC", "LOTAREA", "YEARBLT", "CONDITIONDESC",
                    "FAIRMARKETBUILDING", "FAIRMARKETLAND", "FAIRMARKETTOTAL"]

    for tier_label, tier_mask, show in tiers:
        sub = scored[tier_mask]
        layer = folium.FeatureGroup(
            name=f"Opportunity Score — {tier_label} Priority ({len(sub)} sites)",
            show=show,
        )
        for _, row in sub.iterrows():
            c = centroid(row.geometry)
            if not c:
                continue
            score = row["opportunity_score"]
            color = _score_color(score)

            popup_html = (
                f"<b style='font-size:14px'>Opportunity Score: "
                f"<span style='color:{color}'>{score:.0f}/100</span></b><br>"
                f"<b>Priority Tier:</b> {tier_label}<br>"
                f"<b>Vacancy:</b> {row.get('_class','').replace('_',' ').title()}<br>"
                f"<hr style='margin:4px 0'>"
            )
            for f in popup_fields:
                val = row.get(f)
                if val and str(val).strip() not in ("", "None", "nan", "0"):
                    popup_html += f"<b>{f.replace('_',' ').title()}:</b> {val}<br>"

            folium.CircleMarker(
                location=c,
                radius=float(row["_r"]),
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.8,
                weight=1.5,
                popup=folium.Popup(
                    folium.IFrame(popup_html, width=290, height=230), max_width=310
                ),
                tooltip=f"Score {score:.0f}/100 — {row.get('PROPERTYADDRESS','?')}",
            ).add_to(layer)
        layer.add_to(m)
        log.info("  Opportunity %s: %d sites", tier_label, len(sub))


# ─────────────────────────────────────────────────────────────────────────────
# Layer 4: EPA CIMC sites — colored by remediation status
# ─────────────────────────────────────────────────────────────────────────────

def _cimc_status(row: pd.Series) -> str:
    """
    Green  = brownfield remediated/cleared (BF site with cleanup complete)
    Yellow = brownfield assessment only (not yet characterized)
    Orange = Superfund / NPL (contaminated, federal oversight)
    Gray   = other (RCRA, TRI — informational context)
    """
    bf_assess  = str(row.get("BF_ASSESS_IND",  "")).upper()
    bf_cleanup = str(row.get("BF_CLEANUP_IND", "")).upper()
    sf_code    = str(row.get("SF_NPL_CODE",    "")).strip()
    rcra_done  = str(row.get("RCRA_REMEDY_COMPLT_IND", "")).upper()

    if sf_code and sf_code not in ("", "None", "nan"):
        return "superfund"
    if bf_cleanup == "Y" and rcra_done == "Y":
        return "remediated"
    if bf_cleanup == "Y":
        return "cleanup"
    if bf_assess == "Y":
        return "assessment"
    return "other"


CIMC_STATUS = {
    "remediated": {"color": "#1b5e20", "label": "Brownfield Remediated — potential reuse site", "show": True},
    "cleanup":    {"color": "#e53935", "label": "Brownfield Active Cleanup — not ready",        "show": True},
    "assessment": {"color": "#f9a825", "label": "Brownfield Assessment Only — contamination risk", "show": True},
    "superfund":  {"color": "#6a1b9a", "label": "Superfund / NPL Site",                          "show": False},
    "other":      {"color": "#78909c", "label": "Other EPA-tracked Site (RCRA/TRI)",              "show": False},
}

def add_cimc_sites(m: folium.Map, gdf: gpd.GeoDataFrame):
    log.info("Adding EPA CIMC sites…")
    gdf = gdf.copy()
    gdf["_status"] = gdf.apply(_cimc_status, axis=1)

    popup_fields = ["LOCATION_ADDRESS","CITY_NAME","COUNTY_NAME",
                    "BF_PROPERTY_NAME","BF_ACRES",
                    "SF_SITE_NAME","SF_NPL_CODE",
                    "RCRA_HANDLER_NAME","RCRA_REMEDY_COMPLT_IND"]

    for status, style in CIMC_STATUS.items():
        sub = gdf[gdf["_status"] == status]
        layer = folium.FeatureGroup(
            name=f"EPA CIMC: {style['label']} ({len(sub)})",
            show=style["show"],
        )
        for _, row in sub.iterrows():
            c = centroid(row.geometry)
            if not c:
                continue
            folium.CircleMarker(
                location=c,
                radius=7,
                color=style["color"],
                fill=True, fill_color=style["color"], fill_opacity=0.72,
                weight=2,
                popup=popup(row, popup_fields, style["label"], style["color"]),
                tooltip=row.get("BF_PROPERTY_NAME") or row.get("SF_SITE_NAME") or "EPA site",
            ).add_to(layer)
        layer.add_to(m)
        log.info("  CIMC %s: %d", status, len(sub))


# ─────────────────────────────────────────────────────────────────────────────
# Layer 4: Abandoned mines — context heatmap only
# ─────────────────────────────────────────────────────────────────────────────

def add_mines_heatmap(m: folium.Map, gdf: gpd.GeoDataFrame):
    points = []
    for geom in gdf.geometry:
        c = centroid(geom)
        if c:
            points.append([c[0], c[1], 1.0])
    if not points:
        return
    # Again, add directly to map with show=False via layer name
    HeatMap(
        points,
        name="Abandoned Mines — Legacy Extraction Context (not redevelopable buildings)",
        min_opacity=0.15,
        max_opacity=0.45,
        radius=12,
        blur=16,
        gradient={0.4: "#795548", 0.9: "#3e2723"},
        show=False,
    ).add_to(m)
    log.info("Mines heatmap: %d points", len(points))


# ─────────────────────────────────────────────────────────────────────────────
# Mon Valley community labels
# ─────────────────────────────────────────────────────────────────────────────

COMMUNITIES = [
    ("McKeesport",  40.3468, -79.8448),
    ("Duquesne",    40.3751, -79.8593),
    ("Clairton",    40.2929, -79.8816),
    ("Braddock",    40.4023, -79.8657),
    ("Homestead",   40.4023, -79.9118),
    ("Monessen",    40.1479, -79.8793),
    ("Donora",      40.1748, -79.8590),
    ("Charleroi",   40.1390, -79.8999),
    ("Rankin",      40.4120, -79.8743),
    ("West Mifflin",40.3629, -79.8687),
    ("Bethel Park", 40.3251, -80.0429),
    ("Pittsburgh",  40.4406, -79.9959),
]

def add_community_labels(m: folium.Map):
    layer = folium.FeatureGroup(name="Mon Valley Communities", show=True)
    for name, lat, lon in COMMUNITIES:
        folium.Marker(
            location=[lat, lon],
            tooltip=name,
            icon=folium.DivIcon(
                html=(f'<div style="font-size:11px;font-weight:bold;color:#1a237e;'
                      f'background:rgba(255,255,255,0.9);border:1px solid #1a237e;'
                      f'border-radius:3px;padding:2px 6px;white-space:nowrap;">{name}</div>'),
                icon_size=(len(name)*7, 22),
                icon_anchor=(len(name)*3, 11),
            ),
        ).add_to(layer)
    layer.add_to(m)


# ─────────────────────────────────────────────────────────────────────────────
# HTML overlays
# ─────────────────────────────────────────────────────────────────────────────

TITLE_HTML = """
<div style="position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:1000;
     background:rgba(255,255,255,0.95);border:2px solid #333;border-radius:6px;
     padding:8px 20px;font-size:15px;font-weight:bold;
     box-shadow:2px 2px 6px rgba(0,0,0,0.25);text-align:center;pointer-events:none;">
  AI-Enabled Revitalization: Legacy Industrial Infrastructure<br>
  <span style="font-size:11px;font-weight:normal;color:#555;">
    Southwestern Pennsylvania / Mon Valley &nbsp;·&nbsp; Bubble size = lot area
  </span>
</div>
"""

CONTROL_HTML = """
<div id="ctrl-panel" style="
    position:fixed; top:70px; right:10px; z-index:1000;
    background:white; border:2px solid #555; border-radius:8px;
    box-shadow:2px 2px 10px rgba(0,0,0,0.3);
    font-size:12px; width:240px;">

  <!-- Header -->
  <div style="display:flex;justify-content:space-between;align-items:center;
              padding:8px 12px;background:#37474f;border-radius:6px 6px 0 0;cursor:pointer;"
       onclick="togglePanel()">
    <b style="color:white;font-size:13px;">SW PA Industrial Sites</b>
    <span id="ctrl-toggle-btn" style="color:white;font-size:18px;line-height:1;">−</span>
  </div>

  <!-- Body -->
  <div id="ctrl-body" style="padding:10px 12px;">

    <!-- View toggle -->
    <div style="margin-bottom:10px;">
      <div style="font-size:10px;color:#777;margin-bottom:5px;text-transform:uppercase;letter-spacing:.5px;">View</div>
      <div style="display:flex;gap:4px;">
        <button id="btn-vacancy" onclick="setView('vacancy')" style="
            flex:1;padding:5px 0;font-size:11px;cursor:pointer;border-radius:4px;
            border:2px solid #37474f;background:#37474f;color:white;font-weight:bold;">
          Vacancy Status
        </button>
        <button id="btn-opportunity" onclick="setView('opportunity')" style="
            flex:1;padding:5px 0;font-size:11px;cursor:pointer;border-radius:4px;
            border:2px solid #bbb;background:white;color:#555;">
          Opportunity Score
        </button>
      </div>
    </div>

    <!-- Vacancy legend (shown in vacancy mode) -->
    <div id="vacancy-legend" style="margin-bottom:10px;line-height:1.9;">
      <span style="color:#c62828;font-size:16px;">●</span> Vacant Industrial<br>
      <span style="color:#bf360c;font-size:16px;">●</span> Likely Underutilized<br>
      <span style="color:#1b5e20;font-size:16px;">●</span> Active Industrial
    </div>

    <!-- Opportunity legend (hidden in vacancy mode) -->
    <div id="opportunity-legend" style="display:none;margin-bottom:10px;">
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
        <div style="background:linear-gradient(to right,#1a9641,#ffffbf,#d7191c);
                    width:100px;height:10px;border-radius:3px;flex-shrink:0;"></div>
        <span style="font-size:10px;color:#555;">0 → 100</span>
      </div>
      <div style="font-size:10px;color:#777;line-height:1.5;">
        Scores: vacancy · lot size · dereliction · cost · building age
      </div>
      <div style="margin-top:6px;">
        <label style="display:block;cursor:pointer;">
          <input type="checkbox" id="chk-high" onchange="toggleOpportunityTier('High', this.checked)" checked>
          High priority (&ge;70)
        </label>
        <label style="display:block;cursor:pointer;">
          <input type="checkbox" id="chk-medium" onchange="toggleOpportunityTier('Medium', this.checked)">
          Medium priority (45–70)
        </label>
      </div>
    </div>

    <hr style="margin:6px 0;border:none;border-top:1px solid #eee;">

    <!-- Extra layers -->
    <div style="font-size:10px;color:#777;margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px;">Extra Layers</div>
    <label style="display:block;cursor:pointer;margin-bottom:3px;">
      <input type="checkbox" id="chk-heatmap" onchange="toggleNamedLayer('Density Heatmap', this.checked)" checked>
      Density Heatmap
    </label>
    <label style="display:block;cursor:pointer;margin-bottom:3px;">
      <input type="checkbox" id="chk-communities" onchange="toggleNamedLayer('Mon Valley Communities', this.checked)" checked>
      Community Labels
    </label>
    <label style="display:block;cursor:pointer;margin-bottom:3px;">
      <input type="checkbox" id="chk-mines" onchange="toggleNamedLayer('Abandoned Mines', this.checked)">
      Abandoned Mines (context)
    </label>

    <hr style="margin:6px 0;border:none;border-top:1px solid #eee;">

    <!-- Basemap -->
    <div style="font-size:10px;color:#777;margin-bottom:4px;text-transform:uppercase;letter-spacing:.5px;">Basemap</div>
    <select id="basemap-select" onchange="switchBasemap(this.value)" style="width:100%;font-size:11px;padding:3px;">
      <option value="Satellite (Esri)">Satellite</option>
      <option value="Light (CartoDB)">Light</option>
      <option value="OpenStreetMap">OpenStreetMap</option>
    </select>

    <div style="margin-top:8px;font-size:10px;color:#aaa;text-align:center;">
      Bubble size = lot area &nbsp;·&nbsp; Source: WPRDC
    </div>
  </div>
</div>

<script>
var _map = null;
var _layers = {};       // name fragment → leaflet layer
var _tileLayers = {};   // tile name → leaflet layer
var _currentView = 'vacancy';

// Layer name patterns
var VACANCY_LAYERS     = ['WPRDC: Vacant', 'WPRDC: Likely', 'WPRDC: Active'];
var OPPORTUNITY_LAYERS = ['Opportunity Score'];

function initControl() {
  // Find the leaflet map instance
  _map = Object.values(window).find(function(v) {
    return v && v._leaflet_id && typeof v.eachLayer === 'function' && v._container;
  });
  if (!_map) { setTimeout(initControl, 200); return; }

  // Catalog all layers
  _map.eachLayer(function(layer) {
    var name = layer.options && layer.options.name;
    if (!name) return;
    if (layer._url || layer._tiles) {          // tile layer
      _tileLayers[name] = layer;
    } else {
      _layers[name] = layer;
    }
  });

  // Start in vacancy view (already the default from Python show= flags)
  setView('vacancy');
}

function layersByPattern(patterns) {
  return Object.keys(_layers).filter(function(name) {
    return patterns.some(function(p) { return name.indexOf(p) !== -1; });
  }).map(function(name) { return _layers[name]; });
}

function setView(mode) {
  _currentView = mode;
  var vacLayers = layersByPattern(VACANCY_LAYERS);
  var oppLayers = layersByPattern(OPPORTUNITY_LAYERS);

  if (mode === 'vacancy') {
    // Show vacancy layers (all three)
    vacLayers.forEach(function(l) { _map.addLayer(l); });
    // Hide all opportunity layers
    oppLayers.forEach(function(l) { _map.removeLayer(l); });
    // UI
    styleBtn('btn-vacancy', true);
    styleBtn('btn-opportunity', false);
    document.getElementById('vacancy-legend').style.display = '';
    document.getElementById('opportunity-legend').style.display = 'none';
  } else {
    // Hide all vacancy layers
    vacLayers.forEach(function(l) { _map.removeLayer(l); });
    // Show only tiers whose checkboxes are checked
    oppLayers.forEach(function(l) {
      var name = l.options.name;
      var isHigh   = name.indexOf('High')   !== -1;
      var isMedium = name.indexOf('Medium') !== -1;
      var highChk   = document.getElementById('chk-high').checked;
      var medChk    = document.getElementById('chk-medium').checked;
      if ((isHigh && highChk) || (isMedium && medChk)) {
        _map.addLayer(l);
      } else {
        _map.removeLayer(l);
      }
    });
    // UI
    styleBtn('btn-vacancy', false);
    styleBtn('btn-opportunity', true);
    document.getElementById('vacancy-legend').style.display = 'none';
    document.getElementById('opportunity-legend').style.display = '';
  }
}

function toggleOpportunityTier(tier, show) {
  if (_currentView !== 'opportunity') return;
  layersByPattern(['Opportunity Score']).forEach(function(l) {
    if (l.options.name.indexOf(tier) !== -1) {
      if (show) _map.addLayer(l); else _map.removeLayer(l);
    }
  });
}

function toggleNamedLayer(pattern, show) {
  layersByPattern([pattern]).forEach(function(l) {
    if (show) _map.addLayer(l); else _map.removeLayer(l);
  });
}

function switchBasemap(targetName) {
  // Remove all tile layers, add the selected one
  Object.keys(_tileLayers).forEach(function(name) {
    _map.removeLayer(_tileLayers[name]);
  });
  if (_tileLayers[targetName]) _map.addLayer(_tileLayers[targetName]);
}

function styleBtn(id, active) {
  var btn = document.getElementById(id);
  if (active) {
    btn.style.background = '#37474f';
    btn.style.color = 'white';
    btn.style.borderColor = '#37474f';
  } else {
    btn.style.background = 'white';
    btn.style.color = '#555';
    btn.style.borderColor = '#bbb';
  }
}

function togglePanel() {
  var body = document.getElementById('ctrl-body');
  var btn  = document.getElementById('ctrl-toggle-btn');
  var open = body.style.display !== 'none';
  body.style.display = open ? 'none' : 'block';
  btn.textContent    = open ? '+' : '−';
}

window.addEventListener('load', function() { setTimeout(initControl, 600); });
</script>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    log.info("Building SW PA industrial revitalization map…")

    m = folium.Map(location=MAP_CENTER, zoom_start=MAP_ZOOM, tiles=None)

    # Base tiles
    folium.TileLayer("CartoDB positron",  name="Light (CartoDB)",  control=True).add_to(m)
    folium.TileLayer("OpenStreetMap",     name="OpenStreetMap",    control=True).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satellite (Esri)",
        control=True,
    ).add_to(m)

    # Load data
    cimc    = load("epa_cimc_sites")
    wprdc   = load("wprdc_industrial_properties")
    mines   = load("pasda_abandoned_mines")

    # Layers (back to front)
    # 1. Density heatmap (WPRDC most relevant, then CIMC, then mines for context)
    add_heatmap(m, wprdc, cimc, mines)

    # 2. Mines context heatmap (off by default)
    if mines is not None and len(mines) > 0:
        add_mines_heatmap(m, mines)

    # 3. EPA CIMC sites
    if cimc is not None and len(cimc) > 0:
        add_cimc_sites(m, cimc)

    # 4. WPRDC vacancy bubbles
    if wprdc is not None and len(wprdc) > 0:
        add_wprdc_parcels(m, wprdc)

    # 5. Opportunity score layer (off by default — toggle to switch views)
    if wprdc is not None and len(wprdc) > 0:
        add_opportunity_layer(m, wprdc)

    # 6. Community labels
    add_community_labels(m)

    # UI controls — no LayerControl; replaced by custom CONTROL_HTML panel
    Fullscreen(position="topright").add_to(m)
    MiniMap(toggle_display=True, position="bottomright").add_to(m)

    # HTML overlays
    for html in (TITLE_HTML, CONTROL_HTML):
        m.get_root().html.add_child(folium.Element(html))

    m.save(str(OUTPUT_FILE))
    log.info("Saved → %s", OUTPUT_FILE)
    log.info("Open:   open %s", OUTPUT_FILE)


if __name__ == "__main__":
    main()
