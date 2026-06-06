# KaartKruising

Automated monitoring system that detects intersections between **Flemish environmental permits** (omgevingsvergunningen) and **community paths** (buurtwegen) from the Atlas der Buurtwegen.

## What it does

1. **Discovery** — Fetches recent permits from the Mercator WFS and performs a spatial join with local road data (buurtwegen + wijzigingen)
2. **Filtering** — Only keeps intersections within 19 target municipalities in East Flanders
3. **Mapping** — Generates interactive Leaflet maps with the permit, matched roads, Atlas der Buurtwegen (1841) layer, and aerial photography
4. **Dashboard** — Serves a glassmorphism web dashboard to browse all matches with sorting, filtering, and inline maps

## Requirements

- Python 3.12+
- See `requirements.txt` for dependencies

## Installation

```bash
git clone https://github.com/carameljm/KaartKruising.git
cd KaartKruising
pip install -r requirements.txt
```

## Road data

The script requires two GeoJSON files in the `buurtwegenomgevingsdossiers-main/` directory:

- `buurtwegenoostvlaanderen.geojson` — Atlas der Buurtwegen
- `wijzigingenoostvlaanderen.geojson` — Wijzigingen

Download from [carameljm/buurtwegenomgevingsdossiers](https://github.com/carameljm/buurtwegenomgevingsdossiers).

## Usage

```bash
# Default: check last 7 days
python kruising_checker.py

# Custom lookback period
python kruising_checker.py --days 14

# Custom roads directory and output
python kruising_checker.py --roads-dir ./my-roads --output ./my-maps --days 30
```

### Arguments

| Flag | Default | Description |
|---|---|---|
| `--roads-dir` | `buurtwegenomgevingsdossiers-main` | Directory containing road GeoJSON files |
| `--output` / `-o` | `output_maps` | Output directory for matches.json |
| `--days` | `7` | Number of days to look back for permits |

## How it works

```
WFS (Mercator) → recent permits → spatial join with buurtwegen
  → filter on 19 municipalities → pending_intersections.json
  → generate matches with inline GeoJSON geometry → output_maps/matches.json
  → dashboard renders Leaflet maps directly from geometry data
```

### Pipeline

1. **Discovery**: Finds permit-road intersections and queues them as pending
2. **Processing**: Groups all roads per project, converts geometry to GeoJSON, and writes to `matches.json`
3. **Dashboard**: Reads `matches.json` and renders interactive Leaflet maps inline — no external HTML files needed

> **Note**: Inzageloket validation (checking public availability and fetching deadline dates) is currently disabled because the Inzageloket API now requires JavaScript-based Proof-of-Work (Anubis bot protection). See TODO in `kruising_checker.py` for details on re-enabling.

### Data files

| File | Purpose |
|---|---|
| `pending_intersections.json` | Queue of discovered intersections awaiting processing |
| `output_maps/matches.json` | All matches with inline GeoJSON geometry, served to the dashboard |

Each match in `matches.json` contains `permit_geom` (GeoJSON polygon) and `road_geoms` (array of GeoJSON linestrings) for direct map rendering.

## Dashboard

Open `index.html` in a browser (via a local server due to CORS):

```bash
python -m http.server 8000
# → http://localhost:8000
```

Or view online: [carameljm.github.io/KaartKruising](https://carameljm.github.io/KaartKruising/)

Features:
- Total matches, active count, and municipality count
- Search by project ID
- Filter by municipality and status (active/expired)
- Sort by deadline, date, or municipality
- Active matches sorted by soonest deadline first
- Archived matches (expired deadline) shown below a separator
- Click any match to open an interactive Leaflet map with Atlas der Buurtwegen (1841) and aerial photo layers
- Color-coded deadline indicators (red ≤ 3 days, orange ≤ 7 days, green > 7 days)

## Automation

The included GitHub Actions workflow (`.github/workflows/checker.yml`) runs nightly at 03:00 and auto-commits updated dashboard data.

## Target municipalities

Anzegem, Avelgem, Brakel, Deinze, Gavere, Geraardsbergen, Horebeke, Kluisbergen, Kruisem, Lierde, Maarkedal, Nazareth-De Pinte, Oudenaarde, Ronse, Waregem, Wortegem-Petegem, Zottegem, Zulte, Zwalm

## License

MIT
