"""Loading and number-crunching for the dashboard. No Streamlit in here, so it is easy to test.

Data comes from CSVs on the `data` branch (over HTTP) or from a local folder (for testing).
"""
import io
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
