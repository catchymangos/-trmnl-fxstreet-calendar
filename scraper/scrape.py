import json
import os
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
OUTPUT = os.path.join(DOCS_DIR, "calendar.json")


def scrape():
    api_events = []
    all_urls = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()

        def on_response(resp):
            url = resp.url
            ct = resp.headers.get("content-type", "")
            all_urls.append({"url": url[:200], "status": resp.status, "ct": ct[:50]})

            if "json" not in ct:
                return
            try:
                body = resp.json()
            except Exception:
                return

            print(f"JSON response: {url[:150]} -> type={type(body).__name__}, len={len(body) if isinstance(body, (list, dict)) else 'n/a'}")

            if isinstance(body, list) and len(body) > 0:
                api_events.extend(body)
                print(f"  >> Captured {len(body)} items from list response")
                if len(body) > 0:
                    print(f"  >> First item keys: {list(body[0].keys()) if isinstance(body[0], dict) else 'not a dict'}")
            elif isinstance(body, dict):
                for key in ("items", "data", "eventDates", "events", "value", "result", "calendar", "rows"):
                    if key in body and isinstance(body[key], list) and len(body[key]) > 0:
                        api_events.extend(body[key])
                        print(f"  >> Captured {len(body[key])} items from dict['{key}']")
                        if isinstance(body[key][0], dict):
                            print(f"  >> First item keys: {list(body[key][0].keys())}")
                        return

        page.on("response", on_response)

        print("Loading FXStreet economic calendar...")
        page.goto(
            "https://www.fxstreet.com/economic-calendar",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        print("Page loaded, waiting 15s for JS to render...")
        page.wait_for_timeout(15000)

        print(f"\n=== Network summary: {len(all_urls)} total responses ===")
        for u in all_urls:
            if "fxstreet" in u["url"].lower() or "calendar" in u["url"].lower():
                print(f"  {u['status']} {u['ct'][:30]:30s} {u['url'][:150]}")

        print(f"\n=== API events captured: {len(api_events)} ===")

        if not api_events:
            print("\nTrying DOM fallback...")
            print(f"Page title: {page.title()}")
            print(f"Page URL: {page.url}")
            body_text = page.inner_text("body")[:500]
            print(f"Body text preview: {body_text[:300]}")

            selectors_to_try = [
                "[class*='calendar']", "[class*='Calendar']",
                "[class*='event']", "[class*='Event']",
                "table", "tr[data-eventid]",
                "[data-testid]", "[class*='row']",
                "iframe",
            ]
            for sel in selectors_to_try:
                count = len(page.query_selector_all(sel))
                if count > 0:
                    print(f"  Selector '{sel}': {count} matches")

        browser.close()

    return normalize(api_events)


def _str(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("none", "null") else s


def normalize(raw):
    out = []
    now_iso = datetime.utcnow().isoformat() + "Z"
    for ev in raw:
        try:
            date_raw = ev.get("DateTime") or ev.get("dateTime")
            if isinstance(date_raw, dict):
                date_utc = date_raw.get("Date", "")
            else:
                date_utc = _str(date_raw) or _str(
                    ev.get("DateUtc") or ev.get("dateUtc")
                    or ev.get("date_utc") or ev.get("date") or ""
                )

                        vol = ev.get("Volatility") or ev.get("volatility")
            if vol is not None:
                if isinstance(vol, str) and not vol.isdigit():
                    vol_map = {"high": "High", "medium": "Medium", "low": "Low", "none": "Low"}
                    impact = vol_map.get(vol.strip().lower(), "Low")
                else:
                    v = int(vol)
                    impact = "High" if v >= 3 else ("Medium" if v == 2 else "Low")
            elif ev.get("impact"):
                impact = str(ev["impact"]).capitalize()
            else:
                impact = "Low"

            n = {
                "id":        _str(ev.get("IdEcoCalendarDate") or ev.get("id") or ev.get("eventId") or ""),
                "event_id":  _str(ev.get("IdEcoCalendar") or ev.get("eventTypeId") or ""),
                "name":      _str(ev.get("Name") or ev.get("name") or ev.get("event") or ""),
                "country":   _str(ev.get("InternationalCode") or ev.get("CountryCode") or ev.get("countryCode") or ev.get("country") or "").strip(),
                "date_utc":  date_utc,
                "impact":    impact,
                "actual":    _str(ev.get("DisplayActual") or ev.get("Actual") or ev.get("actual") or ""),
                "consensus": _str(ev.get("DisplayConsensus") or ev.get("Consensus") or ev.get("consensus") or ""),
                "previous":  _str(ev.get("DisplayPrevious") or ev.get("Previous") or ev.get("previous") or ""),
                "better":    ev.get("Better") or ev.get("better") or False,
                "worse":     ev.get("Worst") or ev.get("worst") or False,
                "scraped":   now_iso,
            }
            if n["name"]:
                out.append(n)
        except Exception:
            continue
    return out


def merge_and_save(new_events):
    os.makedirs(DOCS_DIR, exist_ok=True)

    existing = []
    if os.path.exists(OUTPUT):
        with open(OUTPUT) as f:
            try:
                existing = json.load(f)
            except Exception:
                existing = []

    by_key = {}
    for ev in existing:
        by_key[ev.get("id") or (ev.get("name") + ev.get("date_utc", ""))] = ev
    for ev in new_events:
        by_key[ev.get("id") or (ev.get("name") + ev.get("date_utc", ""))] = ev

    cutoff = (datetime.utcnow() - timedelta(weeks=3)).isoformat()
    merged = [ev for ev in by_key.values() if ev.get("date_utc", "z") >= cutoff]
    merged.sort(key=lambda x: x.get("date_utc", ""))

    with open(OUTPUT, "w") as f:
        json.dump(merged, f, indent=2)

    print(f"Saved {len(merged)} events to docs/calendar.json "
          f"({len(new_events)} fresh, {len(existing)} prior)")


if __name__ == "__main__":
    events = scrape()
    merge_and_save(events)
