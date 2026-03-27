# AI-Enabled Revitalization of Legacy Manufacturing Infrastructure
### Southwestern Pennsylvania / Mon Valley

Interactive map and data pipeline supporting a proposal to identify and prioritize legacy industrial sites in Southwestern Pennsylvania for conversion into modern, smart manufacturing environments. Developed in the context of the RK Mellon–supported AI-enabled manufacturing revitalization initiative.

---

## Background

Southwestern Pennsylvania retains a significant inventory of underutilized and abandoned industrial facilities — remnants of its historic steel and manufacturing base. At the same time, advanced manufacturing is re-emerging as a key regional economic driver. The critical gap is converting legacy infrastructure into modern, smart manufacturing environments, a process that is technically complex and often beyond the capabilities of small and mid-sized manufacturers (SMMs).

This project focuses specifically on the **Mon Valley** (Allegheny, Washington, Westmoreland, Fayette, and Greene counties), where historic disinvestment has left both infrastructure and workforce potential underutilized.

---

## What This Tool Does

1. **Fetches** publicly available data on industrial properties, brownfields, and abandoned sites from multiple government APIs — no API keys required
2. **Scores** each site on a 0–100 **Opportunity Score** based on vacancy, lot size, building dereliction, acquisition cost, and building age
3. **Renders** an interactive map with toggleable layers so stakeholders can explore and identify pilot sites

---

## Live Map

Open `swpa_industrial_map.html` in any browser. No server needed.

**Layers (toggle in the top-right panel):**

| Layer | What it shows | On by default |
|---|---|---|
| Density Heatmap | Concentration of all industrial activity across the region | ✅ |
| WPRDC: Vacant Industrial | Tax-assessed industrial parcels classified as vacant | ✅ |
| WPRDC: Likely Underutilized | Industrial parcels where building value < 10% of total (derelict structure) | ✅ |
| WPRDC: Active Industrial | Currently operating industrial sites (lower priority for proposal) | ❌ |
| Opportunity Score — High | Sites scoring ≥ 70/100 on the composite opportunity index | ❌ |
| Opportunity Score — Medium | Sites scoring 45–70/100 | ❌ |
| Mon Valley Communities | Reference labels for key communities | ✅ |
| Abandoned Mines Heatmap | Legacy extraction footprint — **context only, not redevelopment targets** | ❌ |

> **Tip:** Turn off the Vacancy layers and turn on Opportunity Score — High to see the 500+ top-ranked pilot site candidates.

---

## Opportunity Score

Each non-active industrial parcel is scored 0–100 using a weighted composite of five factors derived entirely from the WPRDC property assessment data:

| Factor | Weight | Rationale |
|---|---|---|
| **Vacancy status** | 35% | Vacant parcels are directly available; underutilized score partial credit |
| **Lot area** | 25% | Larger sites can support meaningful manufacturing operations |
| **Building dereliction** | 20% | High land/total value ratio signals a deteriorated or absent structure |
| **Acquisition affordability** | 15% | Lower fair market total = lower barrier to entry for SMMs |
| **Building age** | 5% | Pre-1960 construction is more likely legacy industrial stock |

Scores are only computed for vacant and likely-underutilized parcels. Active industrial sites are excluded as they are not available for redevelopment.

---

## Data Sources

All sources are **free and require no API keys**.

| Source | What | Endpoint |
|---|---|---|
| **WPRDC** — Allegheny Co. Property Assessments | 3,544 industrially-classified parcels with assessment values, year built, condition | `data.wprdc.org` |
| **Allegheny County GIS** — Parcel polygons | Polygon geometry for each parcel, joined to WPRDC by PIN/PARID | `gisdata.alleghenycounty.us` |
| **PA DEP / PASDA** — Abandoned mine reclamation | 2,666 mine reclamation sites (context layer) | `mapservices.pasda.psu.edu` |
| **EPA CIMC** | Brownfield assessment, cleanup, and Superfund sites | `map22.epa.gov` (currently rate-limited) |

### A note on data scope

This tool targets **existing buildings** that can be retrofitted — old factories, warehouses, and machine shops sitting idle. It intentionally excludes:

- **Abandoned mines** — underground excavations and surface reclamation areas are not buildings and cannot be directly converted into manufacturing facilities. They appear as a background heatmap for historical context only.
- **Active EPA cleanup sites** — contaminated land under active remediation is not available for near-term redevelopment.

---

## Setup & Usage

### Requirements

- Python 3.11+
- Internet access (for `fetch_data.py`)

```bash
pip install -r requirements.txt
```

### Run

```bash
# Step 1: Download data from public APIs (~5 minutes)
python fetch_data.py

# Step 2: Build the interactive map
python make_map.py

# Step 3: Open in browser
open swpa_industrial_map.html
```

Data files are written to `data/` as GeoJSON. Re-run `fetch_data.py` periodically to refresh with updated assessments.

### File Structure

```
legacyManufacturing/
├── fetch_data.py            # Data pipeline — fetches from 4 public APIs
├── make_map.py              # Map builder — renders interactive HTML map
├── requirements.txt         # Python dependencies
├── swpa_industrial_map.html # Pre-built map (open directly in browser)
└── data/                    # Downloaded GeoJSON files (generated by fetch_data.py)
    ├── wprdc_industrial_properties.geojson
    ├── allegheny_parcels.geojson
    └── pasda_abandoned_mines.geojson
```

---

## Regional Context

The Mon Valley communities most directly relevant to this proposal:

McKeesport · Duquesne · Clairton · Braddock · Homestead · Monessen · Donora · Charleroi · Rankin · West Mifflin

These communities sit along the Monongahela River corridor where steel production historically concentrated. The density of idle industrial parcels — visible in the heatmap — reflects both the scale of that legacy and the scale of the opportunity.

---

## Connection to the Broader Proposal

This mapping tool is the site-identification component of a larger AI-enabled design framework that integrates:

- **LLM-driven facility assessment** — reasoning about cost, throughput, workforce needs, and sustainability tradeoffs for specific sites
- **Generative AI** — producing redesign concepts from CAD models and equipment specifications
- **Mixed reality interfaces** — allowing stakeholders to walk through proposed layouts in physical space
- **Digital twin simulation** — evaluating operational feasibility before any construction begins

The pilot phase targets 1–2 high-scoring sites from this map for full framework application in partnership with regional stakeholders.
