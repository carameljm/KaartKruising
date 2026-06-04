# KaartKruising

Automated monitoring system that detects intersections between **Flemish environmental permits** (omgevingsvergunningen) and **community paths** (buurtwegen) from the Atlas der Buurtwegen.

## What it does

1. **Discovery** — Fetches recent permits from the Mercator WFS and performs a spatial join with local road data (buurtwegen + wijzigingen)
2. **Filtering** — Only keeps intersections within 19 target municipalities in East Flanders
3. **Validation** — Checks each intersection against the [Omgevingsloket Inzage](https://omgevingsloketinzage.omgeving.vlaanderen.be/) to confirm public availability
4. **Mapping** — Generates interactive Folium maps with the permit, matched roads, Atlas der Buurtwegen (1841) layer, and aerial photography
5. **Dashboard** — Serves a glassmorphism web dashboard to browse all validated matches

## Requirements

- Python 3.10+
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
| `--output` / `-o` | `output_maps` | Output directory for maps and matches.json |
| `--days` | `7` | Number of days to look back for permits |

## How it works

```
WFS (Mercator) → recent permits → spatial join with buurtwegen
  → filter on 19 municipalities → pending_intersections.json
  → validate against Inzageloket → output_maps/matches.json
  → generate Folium maps → dashboard (index.html)
```

### Two-phase pipeline

1. **Phase 1 — Discovery**: Finds permit-road intersections and queues them as pending
2. **Phase 2 — Validation**: Checks pending items against the Inzageloket API with retry logic (3 attempts, exponential backoff). Validated items get a map and appear on the dashboard.

### Data files

| File | Purpose |
|---|---|
| `pending_intersections.json` | Queue of discovered intersections not yet on Inzageloket |
| `output_maps/matches.json` | Validated matches served to the dashboard |
| `output_maps/match_*.html` | Individual interactive Folium maps |

## Dashboard

Open `index.html` in a browser (via a local server due to CORS):

```bash
python -m http.server 8000
# → http://localhost:8000
```

Features:
- Total matches, this-week count, and municipality count
- Search by project ID
- Filter by municipality
- Click-through to interactive maps with Atlas der Buurtwegen (1841) and aerial photo layers

## Automation

The included GitHub Actions workflow (`.github/workflows/checker.yml`) runs nightly at 03:00 and auto-commits updated dashboard data.

## Target municipalities

Anzegem, Avelgem, Brakel, Deinze, Gavere, Geraardsbergen, Horebeke, Kluisbergen, Kruisem, Lierde, Maarkedal, Nazareth-De Pinte, Oudenaarde, Ronse, Waregem, Wortegem-Petegem, Zottegem, Zulte, Zwalm

## License

MIT
