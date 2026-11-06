"""Run all collectors once and append results to CSVs.

Usage: python -m collector.run --data-dir ./data
Each source is isolated: one failing never stops the others.
"""
import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from collector import aep, coop_a, nisc
from collector.common import new_session

SUMMARY_COLS = ["polled_at", "utility", "source_updated_at", "cust_out", "cust_out_exact",
                "cust_served", "outages", "ok", "error"]
AREA_COLS = ["polled_at", "utility", "area_type", "area_id", "area_name", "cust_out",
             "masked", "cust_served", "n_out", "etr"]
INCIDENT_COLS = ["polled_at", "utility", "incident_id", "lat", "lon", "x_raw", "y_raw",
                 "cust_out", "masked", "n_out", "crew_status", "cause", "start_time", "etr",
                 "planned"]
REF_COLS = ["utility", "area_type", "area_id", "area_name", "cust_served", "updated_at"]
STATUS_COLS = ["utility", "last_success", "last_failure", "consecutive_failures",
               "last_error", "last_updated_ms"]

# utility key -> function(session) returning the parsed result. Each runs in isolation.
SOURCES = {aep.UTILITY: aep.fetch}
for _key, _base in coop_a.COOPS.items():
    SOURCES[_key] = lambda session, _b=_base: coop_a.fetch(session, _b)
for _key, (_name, _served) in nisc.COOPS.items():
    SOURCES[_key] = lambda session, _n=_name, _e=_served: nisc.fetch(session, _n, _e)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def append_rows(path, cols, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerows(rows)


def overwrite_rows(path, cols, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def read_status(path):
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {r["utility"]: r for r in csv.DictReader(f)}


def run(data_dir, now=None, sources=None):
    data_dir = Path(data_dir)
    now = now or datetime.now(timezone.utc)
    polled = iso(now)
    month = now.strftime("%Y-%m")
    status_path = data_dir / "status.csv"
    status = read_status(status_path)
    sources = sources or SOURCES
    session = new_session()
    failures = 0

    for name, fetch in sources.items():
        st = status.get(name, {"utility": name, "last_success": "", "last_failure": "",
                               "consecutive_failures": "0", "last_error": "",
                               "last_updated_ms": ""})
        try:
            res = fetch(session)
            if str(res["updated_ms"]) == st["last_updated_ms"]:
                print(f"[{name}] no new data since last poll (skipped)")
            else:
                append_rows(data_dir / "summary" / f"{month}.csv", SUMMARY_COLS, [
                    {"polled_at": polled, "utility": name, "ok": 1, "error": "",
                     **res["summary"]}])
                append_rows(data_dir / "areas" / f"{month}.csv", AREA_COLS,
                            [{"polled_at": polled, "utility": name, **a} for a in res["areas"]])
                append_rows(data_dir / "incidents" / f"{month}.csv", INCIDENT_COLS,
                            [{"polled_at": polled, "utility": name, **i}
                             for i in res.get("incidents", [])])
                st["last_updated_ms"] = str(res["updated_ms"])
                print(f"[{name}] OK: {res['summary']['cust_out']} out, "
                      f"{len(res['areas'])} areas with outages")
            # Reference table: refreshed once a day per utility (other utilities' rows are kept)
            ref_path = data_dir / "reference" / "areas.csv"
            existing = []
            if ref_path.exists():
                with ref_path.open(newline="") as f:
                    existing = list(csv.DictReader(f))
            fresh_today = any(r["utility"] == name and r["updated_at"][:10] == polled[:10]
                              for r in existing)
            if res["reference"] and not fresh_today:
                keep = [r for r in existing if r["utility"] != name]
                overwrite_rows(ref_path, REF_COLS, keep + [
                    {"utility": name, "updated_at": polled, **r} for r in res["reference"]])
            st.update(last_success=polled, consecutive_failures="0", last_error="")
        except Exception as e:  # isolate: record and move on
            failures += 1
            msg = f"{type(e).__name__}: {e}"[:300]
            print(f"[{name}] FAILED: {msg}", file=sys.stderr)
            append_rows(data_dir / "summary" / f"{month}.csv", SUMMARY_COLS, [
                {"polled_at": polled, "utility": name, "ok": 0, "error": msg}])
            st.update(last_failure=polled, last_error=msg,
                      consecutive_failures=str(int(st["consecutive_failures"] or 0) + 1))
        status[name] = st

    overwrite_rows(status_path, STATUS_COLS, list(status.values()))
    # Make 3 failures in a row visible as a failed workflow run.
    stuck = [n for n, s in status.items() if int(s["consecutive_failures"] or 0) >= 3]
    if stuck:
        print(f"ALERT: failing 3+ runs in a row: {stuck}", file=sys.stderr)
    return 1 if stuck else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="data")
    sys.exit(run(p.parse_args().data_dir))
