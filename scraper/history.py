import json
import os
import re
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
CALENDAR = os.path.join(DOCS_DIR, "calendar.json")
HISTORY_DIR = os.path.join(DOCS_DIR, "history")
RECENT = os.path.join(HISTORY_DIR, "recent.json")

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
    (r"Employment Change|Net Change in Employment|Full[- ]Time Employment|Part[- ]Time Employment|Participation Rate", "Employment Change"),
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


def as_bool(v):
    # FXStreet's better/worse arrive as bool or string; normalize to real bool.
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return False


def write_recent():
    # Rolling window of the last 90 days across all monthly files, stable filename for polling.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
    merged = {}
    if os.path.isdir(HISTORY_DIR):
        for fn in os.listdir(HISTORY_DIR):
            if not re.match(r"\d{4}-\d{2}\.json$", fn):
                continue  # only monthly files, skip recent.json itself
            with open(os.path.join(HISTORY_DIR, fn)) as f:
                try:
                    data = json.load(f)
                except Exception:
                    continue
            for k, v in data.items():
                if v.get("date", "") >= cutoff:
                    merged[k] = v
    with open(RECENT, "w") as f:
        json.dump(merged, f, indent=2, sort_keys=True)
    print(f"Wrote recent.json ({len(merged)} records, last 90 days)")


def run():
    if not os.path.exists(CALENDAR):
        print("No calendar.json; nothing to archive.")
        return
    with open(CALENDAR) as f:
        events = json.load(f)
    os.makedirs(HISTORY_DIR, exist_ok=True)
    touched = {}
    added = 0
    updated = 0
    for ev in events:
        actual = (ev.get("actual") or "").strip()
        if not actual:
            continue
        impact = ev.get("impact") or "Low"
        if impact not in ("High", "Medium"):
            continue
        date_utc = ev.get("date_utc") or ""
        if len(date_utc) < 10:
            continue
        day = date_utc[:10]
        country = (ev.get("country") or "").strip()
        base = base_name((ev.get("name") or "").strip())
        key = f"{country}|{base}|{day}"
        mp = month_path(day)
        if mp not in touched:
            touched[mp] = load(mp)
        store = touched[mp]
        record = {
            "country": country,
            "indicator": base,
            "date": day,
            "actual": actual,
            "consensus": (ev.get("consensus") or "").strip(),
            "previous": (ev.get("previous") or "").strip(),
            "unit": ev.get("unit") or "",
            "better": as_bool(ev.get("better")),
            "worse": as_bool(ev.get("worse")),
        }
        if key not in store:
            added += 1
            store[key] = record
        elif not store[key].get("better") and not store[key].get("worse") and (record["better"] or record["worse"]):
            # Backfill directional verdict onto an existing record that lacked it.
            store[key]["better"] = record["better"]
            store[key]["worse"] = record["worse"]
            updated += 1
    for mp, store in touched.items():
        with open(mp, "w") as f:
            json.dump(store, f, indent=2, sort_keys=True)
        print(f"Wrote {mp} ({len(store)} records)")
    write_recent()
    print(f"History updated — {added} new, {updated} backfilled across {len(touched)} month file(s).")


if __name__ == "__main__":
    run()
