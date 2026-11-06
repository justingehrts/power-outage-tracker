"""Run all collectors once and append results to CSVs.

Usage: python -m collector.run --data-dir ./data
Each source is isolated: one failing never stops the others.
"""
import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from collector import aep

SUMMARY_COLS = ["polled_at", "utility", "source_updated_at", "cust_out", "cust_out_exact",
                "cust_served", "outages", "ok", "error"]
AREA_COLS = ["polled_at", "utility", "area_type", "area_id", "area_name", "cust_out",
             "masked", "cust_served", "n_out", "etr"]
REF_COLS = ["utility", "area_type", "area_id", "area_name", "cust_served", "updated_at"]
STATUS_COLS = ["utility", "last_success", "last_failure", "consecutive_failures",
               "last_error", "last_updated_ms"]

# name -> fetch function. Phase 2 adds co-op sources here.
SOURCES = {aep.UTILITY: aep.fetch}


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
    session = aep.new_session()
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
                st["last_updated_ms"] = str(res["updated_ms"])
                print(f"[{name}] OK: {res['summary']['cust_out']} out, "
                      f"{len(res['areas'])} areas with outages")
            # Reference table is refreshed once a day (overwrite, all utilities' rows kept)
            ref_path = data_dir / "reference" / "areas.csv"
            if (not ref_path.exists()
                    or datetime.fromtimestamp(ref_path.stat().st_mtime, timezone.utc).date()
                    != now.date()):
                keep = []
                if ref_path.exists():
                    with ref_path.open(newline="") as f:
                        keep = [r for r in csv.DictReader(f) if r["utility"] != name]
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
