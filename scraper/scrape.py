import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
OUTPUT = os.path.join(DOCS_DIR, "calendar.json")


def scrape():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
        page = ctx.new_page()

        print("Loading FXStreet for auth cookies...")
        page.goto("https://www.fxstreet.com/economic-calendar", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(10000)

        # Extract cookies from the browser context for use in Python.
        # This avoids CORS issues with page.evaluate(fetch()).
        cookies = ctx.cookies()
        cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        print(f"Got {len(cookies)} cookies from browser context.")

        browser.close()

    # Fetch the API from Python (no CORS restrictions).
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=14)).strftime("%Y-%m-%dT00:00:00Z")
    end = (now + timedelta(days=10)).strftime("%Y-%m-%dT23:59:59Z")
    api_url = (
        f"https://calendar-api.fxsstatic.com/en/api/v2/eventDates/{start}/{end}"
        f"?&volatilities=NONE&volatilities=LOW&volatilities=MEDIUM&volatilities=HIGH"
    )

    print(f"Fetching 3-week range via Python: {api_url[:100]}...")
    req = urllib.request.Request(api_url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Cookie": cookie_header,
        "Accept": "application/json",
        "Referer": "https://www.fxstreet.com/economic-calendar",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            print(f"API status: {res.status}")
            events = json.loads(res.read())
    except urllib.error.HTTPError as e:
        print(f"API HTTP error: {e.code} {e.reason}")
        try:
            body = e.read().decode('utf-8', errors='ignore')[:500]
            print(f"Response body: {body}")
        except:
            pass
        raise SystemExit(f"Aborting: API returned HTTP {e.code}.")
    except Exception as e:
        raise SystemExit(f"Aborting: fetch failed: {type(e).__name__}: {e}")

    # Validate the response before trusting it — a bad/empty fetch must not
    # be allowed to overwrite a good calendar.json.
    if not isinstance(events, list):
        raise SystemExit(f"Aborting: API returned {type(events).__name__}, expected a list.")
    if len(events) == 0:
        raise SystemExit("Aborting: API returned an empty list; keeping existing calendar.json.")

    print(f"Fetched {len(events)} events")
    return normalize(events)


def normalize(raw):
    out = []
    vol_map = {"high": "High", "medium": "Medium", "low": "Low", "none": "Low"}
    for ev in raw:
        try:
            date_utc = ev.get("dateUtc") or ""
            vol = ev.get("volatility") or ""
            impact = vol_map.get(str(vol).strip().lower(), "Low") if vol else "Low"
            is_better = ev.get("isBetterThanExpected")
            actual = ev.get("actual")
            consensus = ev.get("consensus")
            name = (ev.get("name") or "").strip()
            if not name:
                continue
            out.append({
                "id": ev.get("id") or "",
                "name": name,
                "country": (ev.get("countryCode") or "").strip(),
                "date_utc": date_utc,
                "impact": impact,
                "actual": str(actual).strip() if actual is not None else "",
                "consensus": str(consensus).strip() if consensus is not None else "",
                "previous": str(ev["previous"]).strip() if ev.get("previous") is not None else "",
                "unit": ev.get("unit") or "",
                "currency": ev.get("currencyCode") or "",
                "better": is_better is True,
                "worse": is_better is False and actual is not None and consensus is not None,
            })
        except Exception as e:
            print(f"Warning: skipping event due to error: {e}", file=sys.stderr)
            continue
    return out


if __name__ == "__main__":
    events = scrape()
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(events, f, indent=2)
    print(f"Wrote {len(events)} events to {OUTPUT}")
