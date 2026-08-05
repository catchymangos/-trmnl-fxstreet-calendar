import json
import os
import re
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
CALENDAR = os.path.join(DOCS_DIR, "calendar.json")
PULSE = os.path.join(DOCS_DIR, "history", "pulse.json")

SPOTLIGHT = ["US", "EMU", "CN", "JP", "UK", "DE", "CA"]

CLASS_WEIGHTS = [
    (r"core cpi|core pce|core personal consumption", 3.0),
    (r"\bcpi\b|consumer price|\bpce\b|^inflation$|harmonized index of consumer", 3.0),
    (r"nonfarm payroll|\bnfp\b|net change in employment|employment change", 3.0),
    (r"gross domestic product|\bgdp\b", 3.0),
    (r"unemployment rate", 3.0),
    (r"interest rate decision|rate decision|deposit facility|refinancing operations", 3.0),
    (r"core ppi|core producer", 1.8),
    (r"\bppi\b|producer price", 1.8),
    (r"retail sales", 1.8),
    (r"manufacturing pmi|services pmi|composite pmi|\bism\b|purchasing managers", 1.8),
    (r"average earnings|wage|hourly earnings", 1.8),
    (r"adp employment|adp jobs", 1.8),
    (r"industrial production", 1.8),
    (r"trade balance", 1.8),
    (r"consumer confidence|consumer sentiment|michigan|zew|ifo|business confidence|economic sentiment", 0.8),
    (r"building permits|housing starts|home sales|house price|home price", 0.8),
    (r"durable goods", 0.8),
    (r"jobless claims|claimant count|continuing claims", 0.8),
    (r"empire state|philadelphia fed|philly fed|dallas fed|richmond fed|chicago fed", 0.8),
    (r"exports|imports|current account", 0.8),
    (r"rig count|crude oil stock|natural gas storage|mortgage application|redbook|api weekly", 0.2),
]

IMPACT_MULT = {"High": 1.5, "Medium": 1.0}
DEADBAND = 0.15  # matches the transform's tone threshold


def class_weight(name):
    n = (name or "").lower()
    for pat, w in CLASS_WEIGHTS:
        if re.search(pat, n):
            return w
    return 0.8


def num(v):
    if v is None:
        return None
    s = re.sub(r"[,%\s$€¥£]", "", str(v)).rstrip("Kk")
    try:
        return float(s)
    except ValueError:
        return None


def is_rate_hold_as_expected(ev):
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
    return dt.weekday() < 5


def sign(tone):
    return 1 if tone > DEADBAND else (-1 if tone < -DEADBAND else 0)


def compute_trends(store, this_week_key):
    """For each economy, walk this week's archive backward and count the
    consecutive same-direction streak ending this week. Uses only stored
    tones (memory) — no new data. Returns {cc: {"dir": -1|0|1, "streak": n}}."""
    weeks = sorted(k for k in store.keys() if re.match(r"^\d{4}-W\d{2}$", k))
    trends = {}
    # every economy that has a row this week
    latest_rows = store.get(this_week_key, {}).get("rows", {})
    for cc in latest_rows.keys():
        streak = 0
        want = None
        for wk in reversed(weeks):
            row = store.get(wk, {}).get("rows", {}).get(cc)
            if not row or not isinstance(row.get("tone"), (int, float)):
                break
            sgn = sign(row["tone"])
            if sgn == 0:
                break
            if want is None:
                want = sgn
            if sgn == want:
                streak += 1
            else:
                break
        trends[cc] = {"dir": want or 0, "streak": streak}
    return trends


def run():
    if not os.path.exists(CALENDAR):
        print("No calendar.json; nothing to score.")
        return
    with open(CALENDAR) as f:
        events = json.load(f)

    now = datetime.now(timezone.utc)
    today = now.date()

    window_days = []
    d = today
    while len(window_days) < 5:
        if market_day(d):
            window_days.append(d.isoformat())
        d -= timedelta(days=1)
    window_set = set(window_days)

    agg = {}
    for ev in events:
        actual = (ev.get("actual") or "").strip()
        if not actual:
            continue
        du = ev.get("date_utc") or ""
        if len(du) < 10 or du[:10] not in window_set:
            continue
        impact = ev.get("impact") or "Low"
        if impact not in IMPACT_MULT:
            continue
        if is_rate_hold_as_expected(ev):
            continue
        better, worse = as_bool(ev.get("better")), as_bool(ev.get("worse"))
        verdict = 1 if better else (-1 if worse else 0)
        if verdict == 0:
            continue
        country = (ev.get("country") or "").strip()
        if not country:
            continue
        w = class_weight(ev.get("name")) * IMPACT_MULT[impact]
        rec = agg.setdefault(country, {"score": 0.0, "weight": 0.0, "drivers": []})
        rec["score"] += verdict * w
        rec["weight"] += w
        rec["drivers"].append((w, ev.get("name") or "", verdict))

    wk = iso_week(now)
    week_rows = {}
    for country, rec in agg.items():
        if rec["weight"] <= 0:
            continue
        tone = rec["score"] / rec["weight"]
        rec["drivers"].sort(key=lambda x: -x[0])
        top = rec["drivers"][0]
        week_rows[country] = {
            "tone": round(tone, 3),
            "weight": round(rec["weight"], 2),
            "driver": top[1],
            "driver_dir": top[2],
            "n": len(rec["drivers"]),
        }

    store = load(PULSE)
    store[wk] = {
        "week": wk,
        "date": today.isoformat(),
        "rows": week_rows,
    }
    # trend detection uses the accumulated archive (its own memory)
    store[wk]["trends"] = compute_trends(store, wk)

    weeks = sorted(store.keys())
    for old in weeks[:-104]:
        del store[old]

    os.makedirs(os.path.dirname(PULSE), exist_ok=True)
    with open(PULSE, "w") as f:
        json.dump(store, f, indent=2, sort_keys=True)
    print(f"Pulse: wrote {wk} with {len(week_rows)} economies scored, trends computed.")


if __name__ == "__main__":
    run()
