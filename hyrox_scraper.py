"""
HYROX Results Scraper
Scrapes race results from https://results.hyrox.com/
Uses Playwright to handle the dynamic JavaScript content.
Optimized for speed: reduced waits, lighter load, API interception, browser reuse.
"""

import argparse
import csv
import re
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# Optimized browser launch args (faster startup, fewer resources)
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-gpu",
    "--no-sandbox",
]


def scrape_hyrox_results(
    season_url: str = "https://results.hyrox.com/season-8/",
    race: str | None = None,
    division: str | None = None,
    workout: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    gender: str | None = None,
    age_group: str | None = None,
    nationality: str | None = None,
    results_per_page: int = 100,
    output_file: Optional[str] = "hyrox_results.csv",
    headless: bool = True,
    debug: bool = False,
) -> list[dict]:
    """
    Scrape HYROX race results using Playwright.

    Args:
        season_url: URL of the season results page (default: Season 25/26)
        race: Filter by race name (e.g., "2025 London Excel")
        division: Filter by division (e.g., "HYROX PRO", "HYROX")
        first_name: Filter by athlete first name
        last_name: Filter by athlete last name
        results_per_page: 25, 50, or 100
        output_file: Path to save CSV output
        headless: Run browser in headless mode
        debug: Save page HTML for debugging

    Returns:
        List of result dictionaries
    """
    all_results = []
    api_results = []  # Captured from XHR if available

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=BROWSER_ARGS)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        def handle_response(response):
            url = response.url
            if response.status != 200:
                return
            content_type = response.headers.get("content-type", "") or ""
            if "json" not in content_type:
                return
            if "list" in url or "results" in url or "pid=list" in url or "mikatiming" in url.lower():
                try:
                    body = response.json()
                    if isinstance(body, (list, dict)) and _looks_like_results(body):
                        api_results.append(body)
                except Exception:
                    pass

        page.on("response", handle_response)

        try:
            print(f"Navigating to {season_url}...")
            page.goto(season_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#default-lists-event_main_group, #form_lists_default", timeout=10000)
            time.sleep(1)

            if debug:
                html_path = Path("debug_page.html")
                html_path.write_text(page.content(), encoding="utf-8")
                print(f"Saved page HTML to {html_path}")

            # Apply filters (only when specified - skip unnecessary waits)
            if race:
                try:
                    page.locator("#default-lists-event_main_group").select_option(
                        label=re.compile(re.escape(race), re.I)
                    )
                    time.sleep(1)
                except Exception as e:
                    if debug:
                        print(f"Race filter failed: {e}")

            if division:
                try:
                    page.locator("#default-lists-event").select_option(
                        label=re.compile(re.escape(division), re.I)
                    )
                    time.sleep(0.5)
                except Exception as e:
                    if debug:
                        print(f"Division filter failed: {e}")

            if workout:
                try:
                    page.locator("#default-lists-ranking").select_option(
                        label=re.compile(re.escape(workout), re.I)
                    )
                except Exception as e:
                    if debug:
                        print(f"Workout filter failed: {e}")

            if last_name:
                page.locator("#default-lists-name").fill(last_name)
            if first_name:
                page.locator("#default-lists-firstname").fill(first_name)
            if gender:
                page.locator("#default-lists-sex").select_option(label=re.compile(re.escape(gender), re.I))
            if age_group:
                page.locator("#default-lists-age_class").select_option(label=re.compile(re.escape(age_group), re.I))
            if nationality:
                page.locator("#default-lists-nation").select_option(label=re.compile(re.escape(nationality), re.I))

            page.locator("#default-num_results").select_option(value=str(min(results_per_page, 100)))

            show_btn = page.locator("#default-submit, button:has-text('Show Results')").first
            show_btn.click()
            page.wait_for_selector(
                "li:has(.type-fullname), li:has(.place-primary), table.results tbody tr, .list-list",
                timeout=15000,
            )
            time.sleep(0.5)

            all_results, headers = _extract_results_from_page(page)

            # Pagination - fetch ALL pages until no more "Next" button
            max_pages = 500  # Safety limit
            page_num = 1
            while page_num < max_pages:
                next_btn = _find_next_page_button(page)
                if next_btn is None:
                    break

                # Scroll next button into view and click
                next_btn.scroll_into_view_if_needed()
                next_btn.click()
                page.wait_for_load_state("domcontentloaded")
                time.sleep(1)

                page_results, _ = _extract_results_from_page(page)
                if not page_results:
                    break

                # Avoid duplicates: use tuple of values as key (works for both list + table layout)
                def _row_key(r):
                    return tuple(str(v) for v in r.values()) if r else ()

                seen = {_row_key(r) for r in all_results}
                new_count = 0
                for r in page_results:
                    key = _row_key(r)
                    if key and key not in seen:
                        seen.add(key)
                        all_results.append(r)
                        new_count += 1
                if new_count == 0:
                    break  # No new results (or all duplicates), we're done

                page_num += 1

            if api_results and debug:
                print("API responses captured:", len(api_results))

        except PlaywrightTimeout as e:
            print(f"Timeout: {e}. Try --visible to see what's loading.")
        except Exception as e:
            print(f"Error: {e}")
            if debug:
                raise
        finally:
            browser.close()

    if all_results and output_file:
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_results[0].keys(), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_results)
        print(f"Saved {len(all_results)} results to {output_file}")
    elif not all_results and not output_file:
        pass  # API mode, no console message needed
    elif not all_results:
        print(
            "No results found. Run with --visible --debug to see the browser and save page HTML for inspection."
        )

    return all_results


def _looks_like_results(data) -> bool:
    """Heuristic: does this JSON look like results data?"""
    if isinstance(data, list):
        return len(data) > 0 and isinstance(data[0], dict)
    if isinstance(data, dict):
        for key in ("data", "results", "rows", "list", "athletes"):
            if key in data and isinstance(data[key], list):
                return True
    return False


def _parse_api_results(api_data: list) -> list[dict]:
    """Convert API response format to our standard format. Returns [] if format unknown."""
    rows = []
    for item in api_data:
        if isinstance(item, list):
            arr = item
        elif isinstance(item, dict):
            arr = item.get("data") or item.get("results") or item.get("rows") or item.get("list") or []
            if not isinstance(arr, list):
                arr = [item]
        else:
            continue
        for r in arr:
            if not isinstance(r, dict):
                continue
            # Map common API field names to our schema
            row = {
                "full_name": (
                    r.get("fullname") or r.get("full_name") or r.get("name")
                    or f"{r.get('lastname', '')}, {r.get('firstname', '')}".strip(", ")
                ),
                "rank_division": str(r.get("place") or r.get("rank") or r.get("rank_division") or r.get("pos") or ""),
                "ag_rank": str(r.get("age_rank") or r.get("ag_rank") or r.get("place2") or ""),
                "nation": str(r.get("nation") or r.get("nation_abbr") or r.get("country") or ""),
                "age_group": str(r.get("age_class") or r.get("age_group") or ""),
            }
            if row["full_name"] or row["rank_division"]:
                rows.append(row)
    return rows


def _find_next_page_button(page):
    print("finding next page")

    try:
        # Scope to pagination container
        pagination = page.locator("ul.pagination")

        if pagination.count() == 0:
            return None

        next_btn = page.locator(
            "ul.pagination li.pages-nav-button a:has-text('›'), \
            ul.pagination li.pages-nav-button a:has-text('»'), \
            ul.pagination li.pages-nav-button a:has-text('>')"
        ).first

        if next_btn.count() > 0 and next_btn.is_visible() and next_btn.is_enabled():
            return next_btn

    except Exception as e:
        print("pagination error:", e)

    return None


def _extract_results_from_page(page) -> tuple[list[dict], list[str]]:
    """
    Extract results from page. Mika Timing uses li elements with specific classes.
    Falls back to table extraction if no list items found.

    Field mappings (from Mika Timing list layout):
    - .list-field.type-fullname -> full_name
    - .list-field.type-place.place-primary -> rank_division
    - .list-field.type-place.place-secondary -> ag_rank
    - .nation__abbr -> nation
    - .list-label (visible-xs-block visible-sm-block) -> age_group
    """
    results = []
    headers = []

    # Try li-based layout first (Mika Timing results list)
    # Select li elements that contain result fields (fullname, place-primary, etc.)
    list_rows = page.locator(
        "li:has(.type-fullname), li:has(.place-primary), li:has(.nation__abbr), "
        "ul.list li, ul.list-results li, .list-list li"
    ).all()

    if list_rows:
        for row in list_rows:
            def _text(sel: str) -> str:
                try:
                    el = row.locator(sel).first
                    if el.is_visible() or "hidden" not in (el.get_attribute("class") or ""):
                        return el.inner_text().strip()
                except Exception:
                    pass
                return ""

            record = {
                "full_name": _text(".list-field.type-fullname") or _text(".type-fullname"),
                "rank_division": _text(".list-field.type-place.place-primary") or _text(".place-primary"),
                "ag_rank": _text(".list-field.type-place.place-secondary") or _text(".place-secondary"),
                "nation": _text(".nation__abbr"),
                "age_group": _text(".type-age_class"),
            }
            # Only add if we got at least full_name or rank
            if record["full_name"] or record["rank_division"]:
                results.append({k: v for k, v in record.items()})

    # Fallback: table layout
    if not results:
        rows = page.locator(
            "table.results tbody tr, table tbody tr, .result-row, .results-table tr, [data-result]"
        ).all()
        if not rows:
            rows = page.locator("tr").filter(has=page.locator("td")).all()

        header_el = page.locator("table thead th, table tr:first-child th").first
        if header_el.is_visible():
            headers = [h.inner_text().strip() for h in page.locator("table thead th, table tr:first-child th").all()]

        for row in rows:
            cells = row.locator("td").all()
            if not cells:
                continue
            texts = [c.inner_text().strip() for c in cells]
            if headers and len(headers) == len(texts):
                results.append(dict(zip(headers, texts)))
            else:
                results.append({f"Column_{i+1}": t for i, t in enumerate(texts)})

    return results, headers


def _extract_select_options(page, selector: str) -> list[dict]:
    """Extract option value/label pairs from a select element."""
    options = []
    try:
        select = page.locator(selector).first
        if not select.is_visible():
            return []
        opts = select.locator("option").all()
        for opt in opts:
            val = opt.get_attribute("value")
            label = opt.inner_text().strip()
            if val is not None and label:
                options.append({"value": val, "label": label})
    except Exception:
        pass
    return options


def fetch_form_options(
    season: int | str = 8,
    race: str | None = None,
    headless: bool = True,
) -> dict:
    """
    Load the HYROX results page and extract form dropdown options (races, divisions, workouts, etc.).

    Args:
        season: Season number (1-8). 8 = 25/26
        race: If provided, select this race first so division options reflect race-specific divisions
        headless: Run browser headlessly

    Returns:
        Dict with keys: races, divisions, workouts, genders, age_groups, nationalities
    """
    season_id = season_string_to_id(str(season)) if isinstance(season, str) else season
    if season_id is None:
        season_id = 8  # fallback
    season_url = f"https://results.hyrox.com/season-{season_id}/"
    result = {
        "races": [],
        "divisions": [],
        "workouts": [],
        "genders": [],
        "age_groups": [],
        "nationalities": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=BROWSER_ARGS)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = context.new_page()

        try:
            page.goto(season_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#default-lists-event_main_group", timeout=10000)
            time.sleep(0.5)

            result["races"] = _extract_select_options(page, "#default-lists-event_main_group")

            if race and result["races"]:
                try:
                    page.locator("#default-lists-event_main_group").select_option(
                        label=re.compile(re.escape(race), re.I)
                    )
                    time.sleep(1)
                except Exception:
                    pass

            # Division options (may be race-dependent)
            result["divisions"] = _extract_select_options(page, "#default-lists-event")

            # Workout options
            result["workouts"] = _extract_select_options(page, "#default-lists-ranking")

            # Gender options
            result["genders"] = _extract_select_options(page, "#default-lists-sex")

            # Age group options
            result["age_groups"] = _extract_select_options(page, "#default-lists-age_class")

            # Nationality options (filter out empty labels like BES, CUW)
            nations = _extract_select_options(page, "#default-lists-nation")
            result["nationalities"] = [n for n in nations if n.get("label")]

        except Exception as e:
            result["error"] = str(e)
        finally:
            browser.close()

    return result


def season_string_to_id(season_str: str) -> int | None:
    """
    Convert season string (e.g. 'Season 22/23') to numeric season_id for URLs.
    Season 18/19=1, 19/20=2, ... 25/26=8.
    """
    m = re.search(r"(\d{2})/\d{2}", season_str)
    if m:
        first_year = int(m.group(1))  # 18, 19, 22, 25, etc.
        return first_year - 17  # 18->1, 22->5, 25->8
    return None


def fetch_seasons(headless: bool = True) -> list[dict]:
    """
    Load the HYROX results page and extract available seasons from the season dropdown.

    Returns:
        List of {"value": "Season 25/26", "label": "Season 25/26"} dicts
    """
    season_url = "https://results.hyrox.com/season-8/"
    seasons = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=BROWSER_ARGS)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = context.new_page()

        try:
            page.goto(season_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(".views .dropdown-menu, .dropdown.views", timeout=10000)
            time.sleep(0.5)

            # Season dropdown: .views .dropdown-menu a or ul.dropdown-menu a
            links = page.locator(".views .dropdown-menu a, .dropdown.views .dropdown-menu a").all()
            for link in links:
                href = link.get_attribute("href") or ""
                label = link.inner_text().strip()
                # href is like "/season-8" or "/season-8/"
                m = re.search(r"season-(\d+)", href)
                if m and label:
                    # Use string label as value (e.g. "Season 25/26")
                    seasons.append({"value": label, "label": label})

        except Exception as e:
            return [{"error": str(e)}]
        finally:
            browser.close()

    return seasons


def main():
    parser = argparse.ArgumentParser(description="Scrape HYROX race results")
    parser.add_argument("--url", default="https://results.hyrox.com/season-8/", help="Season URL")
    parser.add_argument("--race", help="Filter by race name")
    parser.add_argument("--division", help="Filter by division")
    parser.add_argument("--first-name", help="Filter by athlete first name")
    parser.add_argument("--last-name", help="Filter by athlete last name")
    parser.add_argument("--per-page", type=int, default=100, choices=[25, 50, 100])
    parser.add_argument("-o", "--output", default="hyrox_results.csv", help="Output CSV")
    parser.add_argument("--visible", action="store_true", help="Show browser (debug)")
    parser.add_argument("--debug", action="store_true", help="Save HTML and show API URLs")
    args = parser.parse_args()

    scrape_hyrox_results(
        season_url=args.url,
        race=args.race,
        division=args.division,
        first_name=args.first_name,
        last_name=args.last_name,
        results_per_page=args.per_page,
        output_file=args.output,
        headless=not args.visible,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
