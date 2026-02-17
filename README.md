# HYROX Results Scraper

Scrapes race results from [results.hyrox.com](https://results.hyrox.com/season-8/). The site uses dynamic JavaScript (Mika Timing), so this scraper uses **Playwright** to render the page and extract data.

Available as both a **CLI tool** and a **REST API**.

## Setup

1. Install Python dependencies:

```bash
pip install -r requirements.txt
```

2. Install Playwright browsers (one-time):

```bash
playwright install chromium
```

## Usage

**Basic – scrape all results from Season 25/26:**

```bash
python hyrox_scraper.py
```

**Filter by race and division:**

```bash
python hyrox_scraper.py --race "2025 London Excel" --division "HYROX PRO"
```

**Filter by athlete name:**

```bash
python hyrox_scraper.py --first-name "John" --last-name "Smith"
```

**All options:**

```bash
python hyrox_scraper.py --url "https://results.hyrox.com/season-8/" \
  --race "2025 Stockholm" \
  --division "HYROX" \
  --first-name "John" \
  --last-name "Smith" \
  --per-page 100 \
  -o results.csv \
  --visible   # Show browser window (useful for debugging)
```

| Option         | Description                                      |
|----------------|---------------------------------------------------|
| `--url`        | Season results URL (default: season-8 = 25/26)    |
| `--race`       | Filter by race name (e.g., "2025 London")        |
| `--division`   | Filter by division (HYROX PRO, HYROX, etc.)      |
| `--first-name` | Filter by athlete first name                     |
| `--last-name`  | Filter by athlete last name                      |
| `--per-page`   | Results per page: 25, 50, or 100                  |
| `-o`           | Output CSV file path                             |
| `--visible`    | Run with visible browser (for debugging)         |
| `--debug`      | Save page HTML to `debug_page.html`              |

## Output

Results are saved as a CSV file (default: `hyrox_results.csv`) with columns such as rank, name, time, division, etc., depending on the site layout.

## Troubleshooting

- **No results extracted?** Run with `--visible --debug` to see the browser and inspect `debug_page.html`.
- **Timeout errors?** Increase wait times in the script or check your network.
- **Rate limiting?** Add delays between requests if you hit limits.

## API Usage

Start the API server:

```bash
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

Or: `python api.py`

**Interactive docs:** http://localhost:8000/docs

---

### POST /api/results

Fetch race results with filters. Send a JSON body.

**Request body (season, race, division required; workout defaults to "Total"):**

```json
{
  "season": "Season 25/26",
  "race": "2025 London Excel",
  "division": "HYROX PRO",
  "workout": "Total",
  "first_name": "John",
  "last_name": "Smith",
  "gender": "Men",
  "age_group": "25-29",
  "nationality": "United Kingdom",
  "season": "Season 25/26",
  "per_page": 100
}
```

**Example:**

```bash
curl -X POST "http://localhost:8000/api/results" \
  -H "Content-Type: application/json" \
  -d '{"season": "Season 25/26", "race": "2025 London Excel", "division": "HYROX PRO"}'

# With workout (default is Total)
curl -X POST "http://localhost:8000/api/results" \
  -H "Content-Type: application/json" \
  -d '{"season": "Season 25/26", "race": "2025 London Excel", "division": "HYROX PRO", "workout": "1000m SkiErg"}'
```

**Response:**

```json
{
  "success": true,
  "filters": { "race": "2025 London Excel", "division": "HYROX PRO", ... },
  "season_url": "https://results.hyrox.com/season-8/",
  "count": 42,
  "results": [
    { "Rank": "1", "Name": "...", "Time": "...", ... }
  ]
}
```

---

### GET endpoints – form options (site dropdown values)

Fetch the options that populate the filters on the site. Use these to build your UI or validate input.

| Endpoint | Query params | Description |
|----------|--------------|-------------|
| `GET /api/seasons` | — | List of available seasons |
| `GET /api/races` | `season` (required, e.g. "Season 25/26") | List of races for the season |
| `GET /api/divisions` | `season` (required), `race` (optional) | Divisions (pass `race` for race-specific) |
| `GET /api/workouts` | `season` (required) | Workout/ranking options |
| `GET /api/genders` | `season` (required) | Men, Women, Mixed |
| `GET /api/age-groups` | `season` (required) | Age groups (16-24, 25-29, etc.) |
| `GET /api/nationalities` | `season` (required) | Countries |
| `GET /api/options` | `season` (required), `race` (optional) | All options including seasons |

**Examples:**

```bash
# Get available seasons
curl "http://localhost:8000/api/seasons"

# Get races for Season 25/26 (season is required, string like "Season 25/26")
curl "http://localhost:8000/api/races?season=Season%2025%2F26"

# Get divisions for a specific race
curl "http://localhost:8000/api/divisions?season=Season%2025%2F26&race=2025%20London%20Excel"

# Get workouts
curl "http://localhost:8000/api/workouts?season=Season%2025%2F26"

# Get all options at once
curl "http://localhost:8000/api/options?season=Season%2025%2F26"
```

**Options response format (e.g. /api/races):**

```json
{
  "success": true,
  "season": "Season 25/26",
  "races": [
    { "value": "2026 Istanbul", "label": "2026 Istanbul" },
    ...
  ]
}
```

## Seasons

- Season 8 = 25/26: `https://results.hyrox.com/season-8/`
- Season 7 = 24/25: `https://results.hyrox.com/season-7/`
- …and so on for earlier seasons.



