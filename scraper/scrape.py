import json
import os
from datetime import datetime, timedelta, timezone
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
OUTPUT = os.path.join(DOCS_DIR, "calendar.json")


def fetch_fxstreet(start, end):
    url = (
        f"https://calendar-api.fxsstatic.com/en/api/v2/eventDates/"
        f"{start.strftime('%Y-%m-%dT00:00:00Z')}/{end.strftime('%Y-%m-%dT23:59:59Z')}"
        f"?&volatilities=NONE&volatilities=LOW&volatilities=MEDIUM&volatilities=HIGH"
    )
    print(f"Fetching: {url[:120]}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def normalize(raw):
    out = []
    now_iso = datetime.now(timezone.utc).isoformat()
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
                "scraped": now_iso,
            })
        except Exception as e:
            print(f"Skip: {e}")
    out.sort(key=lambda x: x.get("date_utc", ""))
    print(f"Normalized {len(out)} events")
    return out


if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=14)
    end = now + timedelta(days=10)

    raw = fetch_fxstreet(start, end)
    print(f"Fetched {len(raw)} raw events")

    events = normalize(raw)

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(events, f, indent=2)
    print(f"Saved {len(events)} events to calendar.json")
