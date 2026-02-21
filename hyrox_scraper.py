"""
HYROX Results Scraper
Scrapes race results from https://results.hyrox.com/
Optimized: page.evaluate() for fast extraction, HTML fallback, caching.
"""

import argparse
import csv
import hashlib
import json
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

PROFILE_WORKERS = 6  # Parallel browser processes for profile fetching

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# In-memory cache: key -> (results, timestamp)
_RESULTS_CACHE: dict[str, tuple[list[dict], float]] = {}
CACHE_TTL_SECONDS = 3600  # 1 hour

# Optimized browser launch args (faster startup, fewer resources)
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--disable-gpu",
    "--no-sandbox",
]


def _cache_key(season_url: str, race: str | None, division: str | None, workout: str | None,
               first_name: str | None, last_name: str | None, gender: str | None,
               age_group: str | None, nationality: str | None, results_per_page: int,
               fetch_profile_details: bool = False) -> str:
    """Generate cache key from request params."""
    raw = json.dumps([
        season_url, race, division, workout,
        first_name, last_name, gender, age_group, nationality,
        results_per_page, fetch_profile_details,
    ], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_cached(key: str) -> list[dict] | None:
    """Return cached results if valid, else None."""
    if key not in _RESULTS_CACHE:
        return None
    results, ts = _RESULTS_CACHE[key]
    if time.time() - ts > CACHE_TTL_SECONDS:
        del _RESULTS_CACHE[key]
        return None
    return results


def _set_cached(key: str, results: list[dict]) -> None:
    """Store results in cache."""
    _RESULTS_CACHE[key] = (results, time.time())


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
    output_file: Optional[str] = "hyrox_results.json",
    fetch_profile_details: bool = True,
    profile_workers: int = PROFILE_WORKERS,
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

    # Check cache first (only when not fetching profiles - profile data not cached)
    cache_key = _cache_key(
        season_url, race, division, workout,
        first_name, last_name, gender, age_group, nationality,
        results_per_page, fetch_profile_details,
    )
    cached = _get_cached(cache_key) if not fetch_profile_details else None
    if cached is not None:
        if output_file and cached:
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(cached, f, indent=2, ensure_ascii=False)
            print(f"Cache hit: saved {len(cached)} results to {output_file}")
        return cached

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
            page.wait_for_selector("#default-lists-event_main_group,#default-lists-event, #form_lists_default", timeout=10000)
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
                    time.sleep(1.5)
                    # wait until division dropdown reloads with race-specific options
                    page.wait_for_function("""
                        () => {
                            const el = document.querySelector('#default-lists-event');
                            if (!el) return false;
                            return el.options.length > 1;
                        }
                    """, timeout=15000)
                    time.sleep(0.5)
                except Exception as e:
                    if debug:
                        print(f"Race filter failed: {e}")

            if division:
                try:
                    div = division.strip()
                    # Allow hyphen/en-dash/em-dash (site may use – instead of -)
                    div_escaped = re.escape(div.replace("\u2013", "-").replace("\u2014", "-"))
                    div_pat = div_escaped.replace(r"\-", r"[\-\u2013\u2014]") if "-" in div else div_escaped
                    page.locator("#default-lists-event").select_option(
                        label=re.compile(div_pat, re.I)
                    )
                    time.sleep(0.5)
                    # confirm the intended division was selected
                    page.wait_for_function("""
                        (expected) => {
                            const el = document.querySelector('#default-lists-event');
                            if (!el) return false;
                            const selected = el.options[el.selectedIndex];
                            if (!selected) return false;
                            const text = selected.textContent.trim().toLowerCase();
                            const exp = expected.toLowerCase();
                            return text.includes(exp) || exp.includes(text);
                        }
                    """, division.strip(), timeout=10000)
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
            else:
                try:
                    page.locator("#default-lists-ranking").select_option(label=re.compile("Total", re.I))
                except Exception:
                    pass

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

            max_pages = 500
            page_num = 1
            while page_num < max_pages:
                next_btn = _find_next_page_button(page)
                if next_btn is None:
                    break

                next_btn.scroll_into_view_if_needed()
                next_btn.click()
                page.wait_for_load_state("domcontentloaded")
                time.sleep(1)

                page_results, _ = _extract_results_from_page(page)
                if not page_results:
                    break

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
                    break

                page_num += 1

            if fetch_profile_details and all_results:
                n_profiles = len({r.get("profile_link") or "" for r in all_results if (r.get("profile_link") or "").startswith("http")})
                print(f"Fetching profile details for {n_profiles} athletes ({profile_workers} workers)...")
                _fetch_all_profile_details(
                    context, all_results, workers=profile_workers, headless=headless
                )

        except PlaywrightTimeout as e:
            print(f"Timeout: {e}. Try --visible to see what's loading.")
        except Exception as e:
            print(f"Error: {e}")
            if debug:
                raise
        finally:
            if not headless:
                time.sleep(2)  # Pause so user can see results before close
            browser.close()

    if all_results:
        if not fetch_profile_details:
            _set_cached(cache_key, all_results)
        if output_file:
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(all_results, f, indent=2, ensure_ascii=False)
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
    """Find pagination Next button."""
    selectors = [
        "ul.pagination li.pages-nav-button a",
        "ul.pagination li.next a",
        "a.next:not(.disabled)",
        "a[rel='next']",
        "[aria-label='Next']",
        "a:has-text('›')",
        "a:has-text('»')",
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible() and el.is_enabled():
                return el
        except Exception:
            continue
    return None


EXTRACT_JS = """
() => {
  const results = [];
  const rows = document.querySelectorAll('li');
  for (const li of rows) {
    const fn = li.querySelector('.type-fullname');
    if (!fn) continue;
    const anchor = fn.querySelector('a') || fn.closest('a');
    const profile_link = (anchor && anchor.href) ? anchor.href : '';
    const rp = li.querySelector('.place-primary');
    const rs = li.querySelector('.place-secondary');
    const nat = li.querySelector('.nation__abbr');
    const ag = li.querySelector('.list-label') || li.querySelector('.type-age_class');
    const full_name = (fn && fn.textContent) ? fn.textContent.trim() : '';
    const rank_division = (rp && rp.textContent) ? rp.textContent.trim() : '';
    const ag_rank = (rs && rs.textContent) ? rs.textContent.trim() : '';
    const nation = (nat && nat.textContent) ? nat.textContent.trim() : '';
    const age_group = (ag && ag.textContent) ? ag.textContent.replace('Age Group', '').trim() : '';
    if (full_name || rank_division) {
      results.push({ full_name, rank_division, ag_rank, nation, age_group, profile_link });
    }
  }
  return results;
}
"""

EXTRACT_TABLE_JS = """
() => {
  const results = [];
  const rows = document.querySelectorAll('table tbody tr');
  const headerCells = document.querySelectorAll('table thead th');
  const headers = [...headerCells].map(h => h.textContent.trim()).filter(Boolean);
  for (const tr of rows) {
    const cells = tr.querySelectorAll('td');
    if (cells.length === 0) continue;
    const texts = [...cells].map(c => c.textContent.trim());
    const anchor = tr.querySelector('a[href]');
    const profile_link = anchor && anchor.href ? anchor.href : '';
    let obj;
    if (headers.length === texts.length) {
      obj = Object.fromEntries(headers.map((h, i) => [h, texts[i]]));
    } else {
      obj = Object.fromEntries(texts.map((t, i) => ['Column_' + (i+1), t]));
    }
    obj.profile_link = profile_link;
    results.push(obj);
  }
  return results;
}
"""


def _extract_results_from_page(page) -> tuple[list[dict], list[str]]:
    """
    Extract results in ONE round-trip via page.evaluate(). Falls back to
    HTML parse then table evaluate if list layout returns empty.
    """
    # 1. Try list layout (single JS call)
    try:
        results = page.evaluate(EXTRACT_JS)
        if results and isinstance(results, list):
            return results, []
    except Exception:
        pass

    # 2. Fallback: get HTML and parse with BeautifulSoup
    try:
        container = page.locator(".list-list, ul.list, .results-list").first
        if container.count() > 0:
            html = container.inner_html()
            base = page.url.split("/season-")[0] if "/season-" in page.url else "https://results.hyrox.com"
            results = _parse_results_html(html, base)
            if results:
                return results, []
    except Exception:
        pass

    # 3. Fallback: table layout (single JS call)
    try:
        results = page.evaluate(EXTRACT_TABLE_JS)
        if results and isinstance(results, list):
            return results, []
    except Exception:
        pass

    return [], []


def _parse_results_html(html: str, base_url: str = "https://results.hyrox.com") -> list[dict]:
    """Parse results from HTML with BeautifulSoup (fallback when evaluate fails)."""
    results = []
    soup = BeautifulSoup(html, "html.parser")
    for li in soup.select("li"):
        fn = li.select_one(".type-fullname")
        if not fn:
            continue
        anchor = fn.select_one("a") or fn.find_parent("a")
        profile_link = ""
        if anchor and anchor.get("href"):
            href = anchor["href"]
            profile_link = href if href.startswith("http") else f"{base_url.rstrip('/')}{href}" if href.startswith("/") else f"{base_url}/{href}"
        rp = li.select_one(".place-primary")
        rs = li.select_one(".place-secondary")
        nat = li.select_one(".nation__abbr")
        ag = li.select_one(".list-label")
        record = {
            "full_name": (fn.get_text(strip=True) if fn else "") or "",
            "rank_division": (rp.get_text(strip=True) if rp else "") or "",
            "ag_rank": (rs.get_text(strip=True) if rs else "") or "",
            "nation": (nat.get_text(strip=True) if nat else "") or "",
            "age_group": (ag.get_text(strip=True) if ag else "") or "",
            "profile_link": profile_link,
        }
        if record["full_name"] or record["rank_division"]:
            results.append(record)
    return results


# Map detail-box ID to output block name
DETAIL_BOX_NAMES = {
    "detail-box-other": "workout_summary",
    "detail-box-splits": "splits",
    "detail-box-racereplay": "racereplay",
    "detail-box-overalltime": "overalltime",
    "detail-box-judging": "judging_decision",
}

PERSON_DETAIL_JS = """
(boxNames) => {
  const sanitize = (s) => (s || '').trim().replace(/[^a-zA-Z0-9]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
  const result = {};
  const extractTableAsRows = (tbl) => {
    const thead = tbl.querySelector('thead');
    const tbody = tbl.querySelector('tbody');
    if (!thead || !tbody) return null;
    const headerRow = thead.querySelector('tr');
    if (!headerRow) return null;
    const headers = Array.from(headerRow.querySelectorAll('th')).map(th => sanitize(th.textContent.trim()) || 'col');
    const rows = [];
    tbody.querySelectorAll('tr').forEach(tr => {
      const cells = tr.querySelectorAll('td, th');
      if (cells.length === 0) return;
      const row = {};
      cells.forEach((cell, i) => {
        const key = headers[i] || ('col_' + i);
        row[key] = cell.textContent.trim();
      });
      rows.push(row);
    });
    return rows.length > 0 ? rows : null;
  };
  const extractPairs = (container) => {
    const pairs = {};
    container.querySelectorAll('dl').forEach(dl => {
      const dts = dl.querySelectorAll('dt');
      const dds = dl.querySelectorAll('dd');
      dts.forEach((dt, i) => {
        const key = sanitize(dt.textContent) || 'field_' + i;
        if (key) pairs[key] = (dds[i] && dds[i].textContent.trim()) || '';
      });
    });
    container.querySelectorAll('table').forEach(tbl => {
      if (tbl.querySelector('thead')) return;
      tbl.querySelectorAll('tr').forEach(tr => {
        const cells = tr.querySelectorAll('td, th');
        if (cells.length >= 2) {
          const k = sanitize(cells[0].textContent);
          if (k && !pairs[k]) pairs[k] = cells[1].textContent.trim();
        }
      });
    });
    return pairs;
  };
  const extractFromContainer = (container) => {
    const tbl = container.querySelector('table');
    if (tbl && tbl.querySelector('thead')) {
      const rows = extractTableAsRows(tbl);
      if (rows) return { _type: 'table', rows };
    }
    const pairs = extractPairs(container);
    if (Object.keys(pairs).length > 0) return { _type: 'pairs', pairs };
    return null;
  };
  for (const [boxId, blockName] of Object.entries(boxNames)) {
    const box = document.getElementById(boxId);
    if (box) {
      const data = extractFromContainer(box);
      if (data) {
        result[blockName] = data._type === 'table' ? data.rows : data.pairs;
      }
    }
  }
  document.querySelectorAll('[id^="detail-box-"]').forEach(box => {
    const id = box.id;
    const blockName = boxNames[id] || id.replace('detail-box-', '');
    if (!result[blockName]) {
      const data = extractFromContainer(box);
      if (data) {
        result[blockName] = data._type === 'table' ? data.rows : data.pairs;
      }
    }
  });
  return result;
}
"""


def _fetch_person_detail(page, profile_url: str) -> dict:
    """Navigate to profile URL and extract detail data (waits for detail boxes)."""
    if not profile_url or not profile_url.startswith("http"):
        return {}
    try:
        page.goto(profile_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_selector(
            "#detail-box-other, #detail-box-splits",
            state="attached",
            timeout=8000,
        )
        time.sleep(0.3)
        detail = page.evaluate(PERSON_DETAIL_JS, DETAIL_BOX_NAMES)
        return detail if isinstance(detail, dict) else {}
    except Exception:
        return {}


def _fetch_profile_chunk_worker(args: tuple[list[str], bool]) -> list[tuple[str, dict]]:
    """
    Worker for ProcessPoolExecutor: fetch a chunk of profile URLs in a separate process.
    Each process gets its own browser (one tab per worker).
    """
    urls, headless = args
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=BROWSER_ARGS)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = ctx.new_page()
        try:
            for url in urls:
                detail = _fetch_person_detail(page, url)
                results.append((url, detail))
                time.sleep(0.2)
        finally:
            browser.close()
    return results


def _fetch_all_profile_details(
    context,
    results: list[dict],
    workers: int = PROFILE_WORKERS,
    headless: bool = True,
) -> None:
    """Fetch profile details in parallel using multiple browser processes."""
    unique_urls = []
    seen = set()
    for r in results:
        link = r.get("profile_link") or ""
        if link and link.startswith("http") and link not in seen:
            seen.add(link)
            unique_urls.append(link)

    if not unique_urls:
        return

    n_workers = min(workers, len(unique_urls), 10)
    url_chunks = [[] for _ in range(n_workers)]
    for i, url in enumerate(unique_urls):
        url_chunks[i % n_workers].append(url)

    url_to_detail: dict[str, dict] = {}
    task_args = [(chunk, headless) for chunk in url_chunks if chunk]

    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futures = {ex.submit(_fetch_profile_chunk_worker, a): a for a in task_args}
        for fut in as_completed(futures):
            try:
                for url, detail in fut.result():
                    url_to_detail[url] = detail
            except Exception:
                pass

    for r in results:
        link = r.get("profile_link") or ""
        if link and link in url_to_detail and url_to_detail[link]:
            r["profile"] = url_to_detail[link]


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
            # time.sleep(0.5)

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
    parser.add_argument("--race", help="Race name")
    parser.add_argument("--division", help="Division")
    parser.add_argument("--workout", default="Total", help="Workout/ranking type")
    parser.add_argument("--first-name", help="Filter by athlete first name")
    parser.add_argument("--last-name", help="Filter by athlete last name")
    parser.add_argument("--per-page", type=int, default=100, choices=[25, 50, 100])
    parser.add_argument("-o", "--output", default="hyrox_results.json", help="Output JSON file")
    parser.add_argument("--no-profiles", action="store_true", help="Skip fetching profile details")
    parser.add_argument("--profile-workers", type=int, default=PROFILE_WORKERS,
                        help=f"Parallel workers for profile fetching (default: {PROFILE_WORKERS})")
    parser.add_argument("--visible", action="store_true", help="Show browser (debug)")
    parser.add_argument("--debug", action="store_true", help="Save HTML and show API URLs")
    args = parser.parse_args()

    scrape_hyrox_results(
        season_url=args.url,
        race=args.race,
        division=args.division,
        workout=args.workout,
        first_name=args.first_name,
        last_name=args.last_name,
        results_per_page=args.per_page,
        output_file=args.output,
        fetch_profile_details=not args.no_profiles,
        profile_workers=args.profile_workers,
        headless=not args.visible,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
