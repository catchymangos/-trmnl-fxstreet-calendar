import json
import os
import re
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
CALENDAR = os.path.join(DOCS_DIR, "calendar.json")
PULSE = os.path.join(DOCS_DIR, "history", "pulse.json")

# Economies whose pulse we always track (the fixed 7). An 8th "wildcard"
# row is chosen dynamically by the plugin transform, not here.
SPOTLIGHT = ["US", "EMU", "CN", "JP", "UK", "DE", "CA"]

# Indicator-class weights (ratio ~3.75x heavy:light). Matched by regex on the
# release name, most-specific-first. Anything unmatched falls to LIGHT.
CLASS_WEIGHTS = [
    # HEAVY 3.0
    (r"core cpi|core pce|core personal consumption", 3.0),
    (r"\bcpi\b|consumer price|\bpce\b|^inflation$|harmonized index of consumer", 3.0),
    (r"nonfarm payroll|\bnfp\b|net change in employment|employment change", 3.0),
    (r"gross domestic product|\bgdp\b", 3.0),
    (r"unemployment rate", 3.0),
    (r"interest rate decision|rate decision|deposit facility|refinancing operations", 3.0),
    # MEDIUM 1.8
    (r"core ppi|core producer", 1.8),
    (r"\bppi\b|producer price", 1.8),
    (r"retail sales", 1.8),
    (r"manufacturing pmi|services pmi|composite pmi|\bism\b|purchasing managers", 1.8),
    (r"average earnings|wage|hourly earnings", 1.8),
    (r"adp employment|adp jobs", 1.8),
    (r"industrial production", 1.8),
    (r"trade balance", 1.8),
    # LIGHT 0.8
    (r"consumer confidence|consumer sentiment|michigan|zew|ifo|business confidence|economic sentiment", 0.8),
    (r"building permits|housing starts|home sales|house price|home price", 0.8),
    (r"durable goods", 0.8),
    (r"jobless claims|claimant count|continuing claims", 0.8),
    (r"empire state|philadelphia fed|philly fed|dallas fed|richmond fed|chicago fed", 0.8),
    (r"exports|imports|current account", 0.8),
    # NOISE 0.2
    (r"rig count|crude oil stock|natural gas storage|mortgage application|redbook|api weekly", 0.2),
]

IMPACT_MULT = {"High": 1.5, "Medium": 1.0}  # Low excluded


def class_weight(name):
    n = (name or "").lower()
    for pat, w in CLASS_WEIGHTS:
        if re.search(pat, n):
            return w
    return 0.8  # unmatched → light, never zero


def num(v):
    if v is None:
        return None
    s = re.sub(r"[,%\s$€¥£]", "", str(v)).rstrip("Kk")
    try:
        return float(s)
    except ValueError:
        return None


def is_rate_hold_as_expected(ev):
    # Rate decisions only count on a genuine surprise; skip expected holds.
    n = (ev.get("name") or "").lower()
    if re.search(r"rate decision|interest rate decision|deposit facility|refinancing operations", n):
        return not (ev.get("better") or ev.get("worse"))
    return False


def iso_week(dt):
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def load(path):
    if os.path.exists(path):
        with open(path) as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}


def as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return False


def market_day(dt):
    return dt.weekday() < 5  # Mon-Fri


def run():
    if not os.path.exists(CALENDAR):
        print("No calendar.json; nothing to score.")
        return
    with open(CALENDAR) as f:
        events = json.load(f)

    now = datetime.now(timezone.utc)
    today = now.date()

    # rolling window: the last 5 market days (inclusive of today)
    window_days = []
    d = today
    while len(window_days) < 5:
        if market_day(d):
            window_days.append(d.isoformat())
        d -= timedelta(days=1)
    window_set = set(window_days)

    # accumulate weighted score per country over released events in the window
    agg = {}  # country -> {"score": float, "weight": float, "drivers": [(sig, name)]}
    for ev in events:
        actual = (ev.get("actual") or "").strip()
        if not actual:
            continue  # only released events carry a verdict
        du = ev.get("date_utc") or ""
        if len(du) < 10 or du[:10] not in window_set:
            continue
        impact = ev.get("impact") or "Low"
        if impact not in IMPACT_MULT:
            continue  # Low excluded
        if is_rate_hold_as_expected(ev):
            continue
        better, worse = as_bool(ev.get("better")), as_bool(ev.get("worse"))
        verdict = 1 if better else (-1 if worse else 0)
        if verdict == 0:
            continue  # in-line: no directional contribution
        country = (ev.get("country") or "").strip()
        if not country:
            continue
        w = class_weight(ev.get("name")) * IMPACT_MULT[impact]
        rec = agg.setdefault(country, {"score": 0.0, "weight": 0.0, "drivers": []})
        rec["score"] += verdict * w
        rec["weight"] += w
        rec["drivers"].append((w, ev.get("name") or "", verdict))

    # build this week's row per country: normalized tone + top driver
    wk = iso_week(now)
    week_rows = {}
    for country, rec in agg.items():
        if rec["weight"] <= 0:
            continue
        tone = rec["score"] / rec["weight"]  # -1..+1
        rec["drivers"].sort(key=lambda x: -x[0])
        top = rec["drivers"][0]
        week_rows[country] = {
            "tone": round(tone, 3),
            "weight": round(rec["weight"], 2),
            "driver": top[1],
            "driver_dir": top[2],
            "n": len(rec["drivers"]),
        }

    # append to the archive, keyed by ISO week
    store = load(PULSE)
    store[wk] = {
        "week": wk,
        "date": today.isoformat(),
        "rows": week_rows,
    }
    # keep the archive tidy: last 104 weeks (~2 years)
    weeks = sorted(store.keys())
    for old in weeks[:-104]:
        del store[old]

    os.makedirs(os.path.dirname(PULSE), exist_ok=True)
    with open(PULSE, "w") as f:
        json.dump(store, f, indent=2, sort_keys=True)
    print(f"Pulse: wrote {wk} with {len(week_rows)} economies scored.")


if __name__ == "__main__":
    run()
