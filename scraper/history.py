import json
import os
import re
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
CALENDAR = os.path.join(DOCS_DIR, "calendar.json")
HISTORY_DIR = os.path.join(DOCS_DIR, "history")

# Minimal indicator normalizer — mirrors the transform so history keys match the
# base names the plugin groups by. Keep in rough sync with the transform's indicatorMap.
INDICATOR_MAP = [
    (r"Core Consumer Price|Core CPI", "Core CPI"),
    (r"Consumer Price Index|\bCPI\b", "CPI"),
    (r"Harmonized Index of Consumer Prices?", "Inflation"),
    (r"Retail Price Index|\bRPI\b", "RPI"),
    (r"Core Producer Price|Core PPI|PPI Core", "Core PPI"),
    (r"Producer Price Index|\bPPI\b", "PPI"),
    (r"\bPCE\b", "PCE"),
    (r"Gross Domestic Product|\bGDP\b", "GDP"),
    (r"Industrial Production", "Industrial Production"),
    (r"Retail Sales", "Retail Sales"),
    (r"Nonfarm Payrolls|\bNFP\b", "Nonfarm Payrolls"),
    (r"ADP Employment", "ADP Jobs"),
    (r"Employment Change|Full[- ]Time Employment|Part[- ]Time Employment|Participation Rate", "Employment Change"),
    (r"Unemployment Rate", "Unemployment Rate"),
    (r"Average Earnings|Wage", "Wage Growth"),
    (r"Manufacturing PMI", "Manufacturing PMI"),
    (r"Services PMI|Non-Manufacturing PMI", "Services PMI"),
    (r"Interest Rate Decision|Rate Decision", "Rate Decision"),
    (r"Trade Balance", "Trade Balance"),
    (r"Tankan", "Tankan Survey"),
    (r"ZEW.*Current", "ZEW Current Conditions"),
    (r"ZEW", "ZEW Sentiment"),
    (r"IFO", "IFO Business Climate"),
]


def base_name(name):
    for pat, rep in INDICATOR_MAP:
        if re.search(pat, name, re.IGNORECASE):
            return rep
    # fall back to the name before any parenthesis
    return re.split(r"\(", name)[0].strip()


def month_path(day):
    return os.path.join(HISTORY_DIR, day[:7] + ".json")  # day = 'YYYY-MM-DD'


def load(path):
    if os.path.exists(path):
        with open(path) as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}


def run():
    if not os.path.exists(CALENDAR):
        print("No calendar.json; nothing to archive.")
        return
    with open(CALENDAR) as f:
        events = json.load(f)

    os.makedirs(HISTORY_DIR, exist_ok=True)
    touched = {}  # month_path -> dict

    added = 0
    for ev in events:
        actual = (ev.get("actual") or "").strip()
        if not actual:
            continue  # only archive RELEASED events (they have an actual)
        impact = ev.get("impact") or "Low"
        if impact not in ("High", "Medium"):
            continue
        date_utc = ev.get("date_utc") or ""
        if len(date_utc) < 10:
            continue
        day = date_utc[:10]  # YYYY-MM-DD
        country = (ev.get("country") or "").strip()
        base = base_name((ev.get("name") or "").strip())
        key = f"{country}|{base}|{day}"

        mp = month_path(day)
        if mp not in touched:
            touched[mp] = load(mp)
        store = touched[mp]

        # upsert — re-runs overwrite the same key rather than duplicating
        if key not in store:
            added += 1
        store[key] = {
            "country": country,
            "indicator": base,
            "date": day,
            "actual": actual,
            "consensus": (ev.get("consensus") or "").strip(),
            "previous": (ev.get("previous") or "").strip(),
            "unit": ev.get("unit") or "",
        }

    for mp, store in touched.items():
        with open(mp, "w") as f:
            json.dump(store, f, indent=2, sort_keys=True)
        print(f"Wrote {mp} ({len(store)} records)")

    print(f"History updated — {added} new records across {len(touched)} month file(s).")


if __name__ == "__main__":
    run()
