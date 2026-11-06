"""AEP Ohio (Kubra Storm Center) fetcher and parser.

Unofficial, undocumented endpoints: parse defensively and raise (never return
zeros) when the shape is not what we expect.
"""
import time

import requests

BASE = "https://kubra.io"
INSTANCE = "9c0735d8-b721-4dce-b80b-558e98ce1083"
VIEW = "9b2feb80-69f8-4035-925e-f2acbcf1728e"
UTILITY = "aep_ohio"
USER_AGENT = "central-ohio-outage-tracker (newsroom outage monitoring project)"
AREA_TYPES = ("county", "zip")  # Phase 1; district/operatingarea can be added later


class SourceError(Exception):
    """Raised when the source is down or its format changed."""


def get_json(session, url, retries=3, timeout=30):
    """GET JSON with exponential backoff (2s, 4s). Gives up after `retries`."""
    last = None
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
    raise SourceError(f"{url}: {last}")


def new_session():
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _val(node):
    """Return (value, masked) from {"val": 4, "mask": 5}."""
    if not isinstance(node, dict) or "val" not in node:
        raise SourceError(f"unexpected count node: {node!r}")
    return node["val"], int(bool(node.get("mask")))


def fetch(session):
    """Return dict with summary row, area rows, and the source timestamp."""
    state_url = (f"{BASE}/stormcenter/api/v1/stormcenters/{INSTANCE}"
                 f"/views/{VIEW}/currentState?preview=false")
    state = get_json(session, state_url)
    try:
        snapshot = state["data"]["interval_generation_data"]
        deployment = state["stormcenterDeploymentId"]
        updated_ms = state["updatedAt"]
    except KeyError as e:
        raise SourceError(f"currentState missing {e}")

    config = get_json(session, f"{BASE}/stormcenter/api/v1/stormcenters/{INSTANCE}"
                               f"/views/{VIEW}/configuration/{deployment}?preview=false")
    try:
        reports = config["config"]["reports"]["data"]["interval_generation_data"]
    except KeyError as e:
        raise SourceError(f"configuration missing {e}")

    data = get_json(session, f"{BASE}/{snapshot}/public/summary-1/data.json")
    try:
        s = data["summaryFileData"]
        t = s["totals"][0]
        out, masked = _val(t["total_cust_a"])
        summary = {
            "source_updated_at": s["date_generated"],
            "cust_out": out,
            "cust_out_exact": int(not masked),
            "cust_served": t["total_cust_s"],
            "outages": t["total_outages"],
        }
    except (KeyError, IndexError) as e:
        raise SourceError(f"summary missing {e}")

    areas, ref = [], []
    for rep in reports:
        atype = rep.get("areaType")
        if atype not in AREA_TYPES:
            continue
        body = get_json(session, f"{BASE}/{snapshot}/{rep['source']}")
        try:
            rows = body["file_data"]["areas"]
        except KeyError as e:
            raise SourceError(f"{atype} report missing {e}")
        if not rows:
            raise SourceError(f"{atype} report is empty")
        for a in rows:
            out, masked = _val(a["cust_a"])
            ref.append({"area_type": atype, "area_id": a["areaId"], "area_name": a["name"],
                        "cust_served": a["cust_s"]})
            if out > 0:  # zeros are implied by the reference table
                areas.append({"area_type": atype, "area_id": a["areaId"],
                              "area_name": a["name"], "cust_out": out, "masked": masked,
                              "cust_served": a["cust_s"], "n_out": a.get("n_out", ""),
                              "etr": a.get("etr") or ""})
    found = {r["area_type"] for r in ref}
    missing = set(AREA_TYPES) - found
    if missing:
        raise SourceError(f"config has no report for {sorted(missing)}")
    return {"updated_ms": updated_ms, "summary": summary, "areas": areas, "reference": ref}
