"""Loading and number-crunching for the dashboard. No Streamlit in here, so it is easy to test.

Data comes from CSVs on the `data` branch (over HTTP) or from a local folder (for testing).
"""
import io
import json
from datetime import timedelta
from pathlib import Path

import pandas as pd
import requests

DEFAULT_SOURCE = "https://raw.githubusercontent.com/justingehrts/power-outage-tracker/data"
USER_AGENT = "central-ohio-outage-tracker-dashboard"

NAMES = {
    "aep_ohio": "AEP Ohio",
    "south_central_power": "South Central Power",
    "consolidated": "Consolidated Electric",
    "ure": "Union Rural Electric",
    "energy_coop": "The Energy Cooperative",
    "logan_rec": "Logan County Electric",
    "frontier_power": "Frontier Power",
    "gm_energy": "Guernsey-Muskingum",
    "pioneer": "Pioneer Electric",
    "buckeye_rec": "Buckeye Rural Electric",
}

# A source is "stale" if its own timestamp is older than this. AEP sometimes goes close to an
# hour between refreshes, so it gets a longer allowance than the 45-minute default.
STALE_MINUTES = {"default": 45, "aep_ohio": 90}


def display_name(key):
    return NAMES.get(key, key)


def _read_csv(source, relpath):
    """Return a DataFrame, or None if the file does not exist yet."""
    source = str(source)
    if source.startswith("http"):
        r = requests.get(f"{source.rstrip('/')}/{relpath}", timeout=20,
                         headers={"User-Agent": USER_AGENT})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return pd.read_csv(io.StringIO(r.text))
    path = Path(source) / relpath
    return pd.read_csv(path) if path.exists() else None


def months_between(start, end):
    """['2026-10', '2026-11'] for every month touched by [start, end]."""
    months, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def _times(series):
    # source_updated_at has mixed precision (some with nanoseconds), hence ISO8601
    return pd.to_datetime(series, format="ISO8601", utc=True, errors="coerce")


def load_summary(source, months):
    frames = [f for f in (_read_csv(source, f"summary/{m}.csv") for m in months) if f is not None]
    if not frames:
        return pd.DataFrame(columns=["polled_at", "utility", "source_updated_at", "cust_out",
                                     "cust_out_exact", "cust_served", "outages", "ok", "error"])
    df = pd.concat(frames, ignore_index=True)
    df["polled_at"] = _times(df["polled_at"])
    df["source_updated_at"] = _times(df["source_updated_at"])
    return df.sort_values("polled_at").reset_index(drop=True)


def load_status(source):
    df = _read_csv(source, "status.csv")
    if df is None:
        return pd.DataFrame(columns=["utility", "last_success", "last_failure",
                                     "consecutive_failures", "last_error"])
    for col in ("last_success", "last_failure"):
        df[col] = _times(df[col])
    return df.drop(columns=["last_updated_ms"], errors="ignore")


def latest_by_utility(summary, status, now):
    """One row per utility: latest good reading, percent out, and whether it is stale."""
    ok = summary[summary["ok"] == 1]
    last = ok.sort_values("polled_at").groupby("utility").tail(1).set_index("utility")
    last = last[["cust_out", "cust_served", "source_updated_at", "polled_at"]].copy()
    last["pct_out"] = (last["cust_out"] / last["cust_served"] * 100).fillna(0)
    last["age_min"] = (now - last["source_updated_at"]).dt.total_seconds() / 60
    last["stale"] = [age > STALE_MINUTES.get(u, STALE_MINUTES["default"])
                     for u, age in zip(last.index, last["age_min"])]
    fails = status.set_index("utility")["consecutive_failures"] if len(status) else pd.Series(dtype=int)
    last["failing"] = [int(fails.get(u, 0) or 0) > 0 for u in last.index]
    # A configured source with no good reading at all must still be visible
    for u in set(status["utility"]) - set(last.index):
        last.loc[u] = [None, None, pd.NaT, pd.NaT, None, None, True, True]
    return last


def timeseries(summary):
    """Wide table: one row per poll time, one column per utility, in customers out.

    The collector only writes a row when a source's data changed, so values are carried
    forward (a step line) until the next row. Failed polls are ignored, never counted as zero.
    """
    ok = summary[summary["ok"] == 1]
    if ok.empty:
        return pd.DataFrame()
    wide = ok.pivot_table(index="polled_at", columns="utility", values="cust_out", aggfunc="last")
    return wide.sort_index().ffill().fillna(0)


def clip(wide, start, end):
    """Cut to the window. Keeps a starting row at `start` so carried-forward values show."""
    if wide.empty:
        return wide
    before = wide[wide.index < start]
    inside = wide[(wide.index >= start) & (wide.index <= end)]
    if not before.empty:
        seed = before.tail(1).copy()
        seed.index = [start]
        inside = pd.concat([seed, inside])
    return inside


def peak(wide, now, hours=24):
    """(customers out, time) of the highest total in the last `hours` hours, or (0, None)."""
    window = clip(wide, now - timedelta(hours=hours), now)
    if window.empty:
        return 0, None
    total = window.sum(axis=1)
    return int(total.max()), total.idxmax()


def month_list(now, days=7, custom_start=None):
    start = custom_start or (now - timedelta(days=days))
    return months_between(start, now)


def utc_now():
    return pd.Timestamp.now(tz="UTC")


# ---------------------------------------------------------------- Maps (by county)
ASSETS = Path(__file__).parent / "assets"

# One fixed colour per utility so the map and legend stay consistent
COLORS = {
    "aep_ohio": "#4C78A8", "south_central_power": "#F58518", "ure": "#54A24B",
    "energy_coop": "#E45756", "logan_rec": "#72B7B2", "frontier_power": "#EECA3B",
    "gm_energy": "#B279A2", "pioneer": "#9D755D", "buckeye_rec": "#FF9DA6",
    "consolidated": "#BAB0AC",
}


def county_key(name):
    """Normalise county names so 'HARDIN', 'Hardin' and 'Hardin County' all match."""
    return str(name).upper().replace(" COUNTY", "").strip()


def load_geojson():
    return json.loads((ASSETS / "ohio_counties.geojson").read_text())


def county_index(geojson=None):
    """DataFrame [fips, name, key] for Ohio's 88 counties."""
    feats = (geojson or load_geojson())["features"]
    return pd.DataFrame([{"fips": f["id"], "name": f["properties"]["name"],
                          "key": county_key(f["properties"]["name"])} for f in feats])


def load_zip_to_county():
    """{'43001': 'LICKING', ...}: each Ohio ZIP -> the county covering most of it."""
    z = pd.read_csv(ASSETS / "zip_to_county.csv", dtype=str)
    return {r.zip: county_key(r.county_name) for r in z.itertuples()}


def load_reference(source):
    df = _read_csv(source, "reference/areas.csv")
    return df if df is not None else pd.DataFrame(
        columns=["utility", "area_type", "area_id", "area_name", "cust_served", "updated_at"])


def load_areas(source, months):
    frames = [f for f in (_read_csv(source, f"areas/{m}.csv") for m in months) if f is not None]
    if not frames:
        return pd.DataFrame(columns=["polled_at", "utility", "area_type", "area_id", "area_name",
                                     "cust_out", "masked", "cust_served", "n_out", "etr"])
    df = pd.concat(frames, ignore_index=True)
    df["polled_at"] = _times(df["polled_at"])
    return df


def _to_counties(rows, value_col, county_utilities, zip_map):
    """Turn area rows into [utility, county, value]. Utilities that report counties use those;
    the others (ZIP-only co-ops) are placed by mapping each ZIP to its main county."""
    out = []
    for u, g in rows.groupby("utility"):
        if u in county_utilities:
            g = g[g["area_type"] == "county"]
            keys = g["area_name"].map(county_key)
        else:
            g = g[g["area_type"] == "zip"]
            keys = g["area_id"].astype(str).map(zip_map)   # ZIPs we cannot place are dropped
        part = pd.DataFrame({"utility": u, "county": keys, "value": g[value_col]}).dropna()
        out.append(part.groupby(["utility", "county"], as_index=False)["value"].sum())
    cols = ["utility", "county", value_col]
    if not out:
        return pd.DataFrame(columns=cols)
    return pd.concat(out, ignore_index=True).rename(columns={"value": value_col})


def served_by_county(reference, zip_map, valid_counties):
    """Customers each tracked utility serves in each Ohio county."""
    county_utils = set(reference.loc[reference["area_type"] == "county", "utility"])
    df = _to_counties(reference, "cust_served", county_utils, zip_map)
    df = df[df["county"].isin(valid_counties) & (df["cust_served"] > 0)]
    return df.reset_index(drop=True)


def outages_by_county(areas, summary, reference, zip_map, valid_counties):
    """Customers out per utility per county, from each utility's latest good reading."""
    ok = summary[summary["ok"] == 1]
    latest = ok.sort_values("polled_at").groupby("utility").tail(1)[["utility", "polled_at"]]
    cur = areas.merge(latest, on=["utility", "polled_at"])   # only rows from that same poll
    county_utils = set(reference.loc[reference["area_type"] == "county", "utility"])
    df = _to_counties(cur, "cust_out", county_utils, zip_map)
    return df[df["county"].isin(valid_counties)].reset_index(drop=True)


def _detail(group, col):
    g = group.sort_values(col, ascending=False)
    return "<br>".join(f"{display_name(u)}: {int(v):,}" for u, v in zip(g["utility"], g[col]))


BIG_UTILITIES = {"aep_ohio"}   # investor-owned; co-ops are shown ahead of these (see below)
COOP_MIN_SHARE = 0.20          # a co-op shows if it serves at least this share of tracked customers
MIN_CUSTOMERS = 100            # fewer than this in a county is fringe service: does not colour it


def main_utility(g):
    """Which utility colours a county. Co-ops with a real presence win over the big utility
    (otherwise AEP would hide every co-op); utilities with only a handful of customers are ignored."""
    g = g[g["cust_served"] >= MIN_CUSTOMERS]
    if g.empty:
        return None
    share = g["cust_served"] / g["cust_served"].sum()
    coops = g[~g["utility"].isin(BIG_UTILITIES) & (share >= COOP_MIN_SHARE)]
    pool = coops if not coops.empty else g
    return pool.loc[pool["cust_served"].idxmax(), "utility"]


def territory_table(served, index):
    """Per county: the utility that colours it, and a hover list of every tracked utility."""
    rows = []
    for _, c in index.iterrows():
        g = served[served["county"] == c["key"]]
        rows.append({"fips": c["fips"], "county": c["name"], "key": c["key"],
                     "main": main_utility(g) if not g.empty else None,
                     "detail": _detail(g, "cust_served") if not g.empty else "No tracked utility"})
    return pd.DataFrame(rows)


def percent_out_table(served, out, index):
    """Per county: customers out and served (all tracked utilities combined) and percent out."""
    tot_s = served.groupby("county")["cust_served"].sum()
    tot_o = out.groupby("county")["cust_out"].sum()
    rows = []
    for _, c in index.iterrows():
        s_, o_ = tot_s.get(c["key"], 0), tot_o.get(c["key"], 0)
        g = out[(out["county"] == c["key"]) & (out["cust_out"] > 0)]
        rows.append({"fips": c["fips"], "county": c["name"], "cust_out": int(o_),
                     "cust_served": int(s_),
                     "pct_out": (o_ / s_ * 100) if s_ else None,
                     "detail": _detail(g, "cust_out") if len(g) else "None out"})
    return pd.DataFrame(rows)
