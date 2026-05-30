import json
import os
from datetime import datetime, timedelta, timezone
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
            ct = resp.headers.get("content-type", "")
            if "json" not in ct:
                return
            try:
                body = resp.json()
            except Exception:
                return
            if isinstance(body, list) and len(body) > 0 and isinstance(body[0], dict):
                if "dateUtc" in body[0] or "DateUtc" in body[0] or "name" in body[0]:
                    api_events.extend(body)
                    print(f"Captured {len(body)} calendar events from {resp.url[:100]}")
            elif isinstance(body, dict):
                for key in ("items", "data", "eventDates", "events", "value"):
                    if key in body and isinstance(body[key], list) and len(body[key]) > 0:
                        api_events.extend(body[key])
                        print(f"Captured {len(body[key])} events from dict['{key}']")
                        return

        page.on("response", on_response)

        print("Loading FXStreet economic calendar...")
        page.goto(
            "https://www.fxstreet.com/economic-calendar",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        print("Waiting 15s for calendar data to load...")
        page.wait_for_timeout(15000)
        browser.close()

    print(f"Total events captured: {len(api_events)}")
    return normalize(api_events)


def _str(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("none", "null") else s


def normalize(raw):
    out = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for ev in raw:
        try:
            date_utc = _str(
                ev.get("dateUtc") or ev.get("DateUtc")
                or ev.get("date_utc") or ev.get("date") or ""
            )
            if not date_utc:
                date_raw = ev.get("DateTime") or ev.get("dateTime")
                if isinstance(date_raw, dict):
                    date_utc = date_raw.get("Date", "")
                else:
                    date_utc = _str(date_raw)

            vol = ev.get("volatility") or ev.get("Volatility")
            if vol is not None:
                if isinstance(vol, str) and not vol.isdigit():
                    vol_map = {"high": "High", "medium": "Medium", "low": "Low", "none": "Low"}
                    impact = vol_map.get(vol.strip().lower(), "Low")
                else:
                    v = int(vol)
                    impact = "High" if v >= 3 else ("Medium" if v == 2 else "Low")
            else:
                impact = "Low"

            is_better = ev.get("isBetterThanExpected")
            actual = ev.get("actual")
            consensus = ev.get("consensus")

            n = {
                "id":        _str(ev.get("id") or ev.get("IdEcoCalendarDate") or ev.get("eventId") or ""),
                "name":      _str(ev.get("name") or ev.get("Name") or ""),
                "country":   _str(ev.get("countryCode") or ev.get("CountryCode") or ev.get("country") or "").strip(),
                "date_utc":  date_utc,
                "impact":    impact,
                "actual":    _str(actual) if actual is not None else "",
                "consensus": _str(consensus) if consensus is not None else "",
                "previous":  _str(ev.get("previous")) if ev.get("previous") is not None else "",
                "unit":      _str(ev.get("unit") or ""),
                "currency":  _str(ev.get("currencyCode") or ""),
                "better":    is_better is True,
                "worse":     is_better is False and actual is not None and consensus is not None,
                "scraped":   now_iso,
            }
            if n["name"]:
                out.append(n)
        except Exception as e:
            print(f"Normalize error: {e} for event: {ev.get('name', 'unknown')}")
            continue
    print(f"Normalized {len(out)} events from {len(raw)} raw")
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

    cutoff = (datetime.now(timezone.utc) - timedelta(weeks=3)).isoformat()
    merged = [ev for ev in by_key.values() if ev.get("date_utc", "z") >= cutoff]
    merged.sort(key=lambda x: x.get("date_utc", ""))

    with open(OUTPUT, "w") as f:
        json.dump(merged, f, indent=2)

    print(f"Saved {len(merged)} events ({len(new_events)} fresh, {len(existing)} prior)")


if __name__ == "__main__":
    events = scrape()
    merge_and_save(events)
