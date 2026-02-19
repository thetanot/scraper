"""
HYROX Results API
REST API to scrape and return HYROX race results.
"""

from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from hyrox_scraper import fetch_form_options, fetch_seasons, scrape_hyrox_results, season_string_to_id


class ResultsRequest(BaseModel):
    """Request body for POST /api/results"""

    season: str = Field(..., description="Season string (required, e.g. 'Season 25/26'). Use GET /api/seasons for values.")
    race: str = Field(..., description="Race name (required, e.g. '2025 London Excel'). Use GET /api/races for values.")
    division: str = Field(
        ...,
        description="Division (required, e.g. HYROX PRO, HYROX). Use GET /api/divisions for values.",
    )
    workout: str = Field(
        "Total",
        description="Workout/ranking type (default: Total). Use GET /api/workouts for values.",
    )
    first_name: Optional[str] = Field(None, description="Filter by athlete first name")
    last_name: Optional[str] = Field(None, description="Filter by athlete last name")
    gender: Optional[str] = Field(None, description="Filter by gender (Men, Women, Mixed)")
    age_group: Optional[str] = Field(None, description="Filter by age group (e.g., '25-29', '30-34')")
    nationality: Optional[str] = Field(None, description="Filter by nationality (e.g., 'United Kingdom')")
    per_page: int = Field(100, ge=25, le=100, description="Results per page (25, 50, or 100)")
    fetch_profile_details: bool = Field(
        True,
        description="Fetch each athlete's profile page and include detail data",
    )


app = FastAPI(
    title="HYROX Results API",
    description="Scrape HYROX race results. Use GET endpoints to fetch form options, POST to get results.",
    version="1.0.0",
)


@app.post("/api/results")
def post_results(body: ResultsRequest):
    """
    Scrape HYROX results with filters. Send filters in JSON body.
    """
    season_id = season_string_to_id(body.season)
    if season_id is None:
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid season. Use format 'Season 25/26'. Call GET /api/seasons for valid values."},
        )
    season_url = f"https://results.hyrox.com/season-{season_id}/"

    try:
        results = scrape_hyrox_results(
            season_url=season_url,
            race=body.race,
            division=body.division,
            workout=body.workout,
            first_name=body.first_name,
            last_name=body.last_name,
            gender=body.gender,
            age_group=body.age_group,
            nationality=body.nationality,
            results_per_page=body.per_page,
            output_file="justsome.json",
            fetch_profile_details=body.fetch_profile_details,
            headless=False,
            debug=False,
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e),
                "filters": body.model_dump(),
            },
        )

    return {
        "success": True,
        "filters": body.model_dump(),
        "season_url": season_url,
        "count": len(results),
        "results": results,
    }


@app.get("/api/seasons")
def get_seasons():
    """Fetch list of available seasons as shown on the site."""
    seasons = fetch_seasons(headless=True)
    if seasons and isinstance(seasons[0], dict) and "error" in seasons[0]:
        return JSONResponse(status_code=500, content={"error": seasons[0]["error"]})
    return {"success": True, "seasons": seasons}


def _validate_season(season: str) -> JSONResponse | None:
    """Return error response if invalid, else None."""
    if season_string_to_id(season) is None:
        return JSONResponse(
            status_code=422,
            content={"detail": "Invalid season. Use format 'Season 25/26'. Call GET /api/seasons for valid values."},
        )
    return None


@app.get("/api/races")
def get_races(
    season: str = Query(..., description="Season string (required, e.g. 'Season 25/26'). Use GET /api/seasons for values."),
):
    """Fetch list of race options as shown on the site. Season is required."""
    err = _validate_season(season)
    if err:
        return err
    options = fetch_form_options(season=season, headless=True)
    if "error" in options:
        return JSONResponse(status_code=500, content={"error": options["error"]})
    return {"success": True, "season": season, "races": options["races"]}


@app.get("/api/divisions")
def get_divisions(
    season: str = Query(..., description="Season string (required, e.g. 'Season 25/26'). Use GET /api/seasons for values."),
    race: Optional[str] = Query(
        None,
        description="Optional. Select race first to get race-specific division options.",
    ),
):
    """Fetch list of division options. Season is required. Optionally pass race for race-specific divisions."""
    err = _validate_season(season)
    if err:
        return err
    options = fetch_form_options(season=season, race=race, headless=True)
    if "error" in options:
        return JSONResponse(status_code=500, content={"error": options["error"]})
    return {
        "success": True,
        "season": season,
        "race": race,
        "divisions": options["divisions"],
    }


@app.get("/api/workouts")
def get_workouts(
    season: str = Query(..., description="Season string (required, e.g. 'Season 25/26'). Use GET /api/seasons for values."),
):
    """Fetch list of workout/ranking options (Total, 1000m SkiErg, etc.)."""
    err = _validate_season(season)
    if err:
        return err
    options = fetch_form_options(season=season, headless=True)
    if "error" in options:
        return JSONResponse(status_code=500, content={"error": options["error"]})
    return {"success": True, "season": season, "workouts": options["workouts"]}


@app.get("/api/genders")
def get_genders(
    season: str = Query(..., description="Season string (required, e.g. 'Season 25/26'). Use GET /api/seasons for values."),
):
    """Fetch list of gender options (Men, Women, Mixed)."""
    err = _validate_season(season)
    if err:
        return err
    options = fetch_form_options(season=season, headless=True)
    if "error" in options:
        return JSONResponse(status_code=500, content={"error": options["error"]})
    return {"success": True, "season": season, "genders": options["genders"]}


@app.get("/api/age-groups")
def get_age_groups(
    season: str = Query(..., description="Season string (required, e.g. 'Season 25/26'). Use GET /api/seasons for values."),
):
    """Fetch list of age group options (16-24, 25-29, etc.)."""
    err = _validate_season(season)
    if err:
        return err
    options = fetch_form_options(season=season, headless=True)
    if "error" in options:
        return JSONResponse(status_code=500, content={"error": options["error"]})
    return {"success": True, "season": season, "age_groups": options["age_groups"]}


@app.get("/api/nationalities")
def get_nationalities(
    season: str = Query(..., description="Season string (required, e.g. 'Season 25/26'). Use GET /api/seasons for values."),
):
    """Fetch list of nationality/country options."""
    err = _validate_season(season)
    if err:
        return err
    options = fetch_form_options(season=season, headless=True)
    if "error" in options:
        return JSONResponse(status_code=500, content={"error": options["error"]})
    return {"success": True, "season": season, "nationalities": options["nationalities"]}


@app.get("/api/options")
def get_all_options(
    season: str = Query(..., description="Season string (required, e.g. 'Season 25/26'). Use GET /api/seasons for values."),
    race: Optional[str] = Query(None, description="Optional. For race-specific divisions."),
):
    """Fetch all form options in one call (seasons, races, divisions, workouts, genders, age_groups, nationalities)."""
    err = _validate_season(season)
    if err:
        return err
    seasons = fetch_seasons(headless=True)
    if seasons and isinstance(seasons[0], dict) and "error" in seasons[0]:
        return JSONResponse(status_code=500, content={"error": seasons[0]["error"]})

    options = fetch_form_options(season=season, race=race, headless=True)
    if "error" in options:
        return JSONResponse(status_code=500, content={"error": options["error"]})

    return {
        "success": True,
        "season": season,
        "race": race,
        "seasons": seasons,
        **{k: v for k, v in options.items() if k != "error"},
    }


@app.get("/api/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
