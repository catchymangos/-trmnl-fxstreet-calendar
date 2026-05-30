import json
import os
import sys
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
OUTPUT = os.path.join(DOCS_DIR, "calendar.json")


def scrape():
    api_events = []

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
            url = resp.url.lower()
            hit = any(d in url for d in [
                "calendar-api.fxstreet.com",
                "calendar.fxstreet.com",
                "fxstreet.com/proxy/calendar",
                "fxstreet.com/api/calendar",
                "eco-calendar",
            ])
            if not hit:
                return
            try:
                body = resp.json()
            except Exception:
                return
            if isinstance(body, list):
                api_events.extend(body)
            elif isinstance(body, dict):
                for key in ("items", "data", "eventDates", "events", "value"):
                    if key in body and isinstance(body[key], list):
                        api_events.extend(body[key])
                        return

        page.on("response", on_response)

        print("Loading FXStreet economic calendar...")
        page.goto(
            "https://www.fxstreet.com/economic-calendar",
                        wait_until="domcontentloaded",
            timeout=60000,
        )
        page.wait_for_timeout(8000)

        dom_events = []
        if not api_events:
            print("No API data intercepted - trying DOM fallback...")
            dom_events = parse_dom(page)

        browser.close()

    raw = api_events or dom_events
    print(f"Captured {len(raw)} raw events (source: {'api' if api_events else 'dom'})")
    if not raw:
        print("WARNING: No events captured. Page structure may have changed.")

    return normalize(raw)


SELECTOR_SETS = [
    {
        "row":       "[class*='calendarRow'], [class*='EventRow'], tr[data-eventid]",
        "name":      "[class*='eventTitle'], [class*='eventName'], [class*='event__title']",
        "time":      "[class*='eventTime'], [class*='event__time'], time",
        "country":   "[class*='country'], [class*='flag']",
        "actual":    "[class*='actual']",
        "consensus": "[class*='consensus'], [class*='forecast']",
        "previous":  "[class*='previous']",
        "impact":    "[class*='volatility'], [class*='impact'], [class*='bull']",
    },
]


def parse_dom(page):
    events = []
    for sel in SELECTOR_SETS:
        rows = page.query_selector_all(sel["row"])
        if not rows:
            continue
        for row in rows:
            ev = {}
            for field in ("name", "time", "country", "actual", "consensus", "previous"):
                el = row.query_selector(sel[field])
                ev[field] = el.inner_text().strip() if el else ""
            imp_el = row.query_selector(sel["impact"])
            if imp_el:
                cls = (imp_el.get_attribute("class") or "").lower()
                if "high" in cls or "3" in cls:
                    ev["impact"] = "High"
                elif "medium" in cls or "2" in cls:
                    ev["impact"] = "Medium"
                else:
                    ev["impact"] = "Low"
            ev["date_utc"] = row.get_attribute("data-date") or row.get_attribute("data-eventdate") or ""
            ev["source"] = "dom"
            if ev.get("name"):
                events.append(ev)
        if events:
            break
    return events


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
