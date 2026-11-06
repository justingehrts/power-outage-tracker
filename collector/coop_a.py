"""Co-op platform A: South Central Power and Consolidated Electric (plain JSON files)."""
from collector.common import SourceError, get_json, iso_utc

COOPS = {
    "south_central_power": "https://outage.southcentralpower.com",
    "consolidated": "https://outagewatch.consolidated.coop",
}


def fetch(session, base):
    s = get_json(session, f"{base}/data/outageSummary.json")
    try:
        updated = iso_utc(s["updateTime"])
        summary = {
            "source_updated_at": updated,
            "cust_out": s["customersOutNow"],
            "cust_out_exact": 1,
            "cust_served": s["customersServed"],
        }
    except (KeyError, TypeError, ValueError) as e:
        raise SourceError(f"outageSummary unexpected: {e!r}")
    if not isinstance(summary["cust_out"], int) or not summary["cust_served"]:
        raise SourceError(f"outageSummary has odd values: {summary}")

    raw_incidents = get_json(session, f"{base}/data/outages.json")
    if not isinstance(raw_incidents, list):
        raise SourceError("outages.json is not a list")
    incidents = []
    try:
        for o in raw_incidents:
            incidents.append({
                "incident_id": o["outageRecID"],
                "lat": o["outagePoint"]["lat"], "lon": o["outagePoint"]["lng"],
                "cust_out": o["customersOutNow"], "masked": 0, "n_out": 1,
                "crew_status": "assigned" if o.get("crewAssigned") else "",
                "cause": o.get("cause") or "",
                "start_time": iso_utc(o.get("outageStartTime")),
                "etr": iso_utc(o.get("estimatedTimeOfRestoral")),
                "planned": int(bool(o.get("isPlanned"))),
            })
    except (KeyError, TypeError) as e:
        raise SourceError(f"outages.json unexpected: {e!r}")
    summary["outages"] = len(incidents)

    areas, ref = [], []
    groups = get_json(session, f"{base}/data/boundaries.json")  # [] is normal for Consolidated
    if not isinstance(groups, list):
        raise SourceError("boundaries.json is not a list")
    try:
        for g in groups:
            if g["name"] != "Counties":
                continue
            for b in g["boundaries"]:
                ref.append({"area_type": "county", "area_id": b["name"],
                            "area_name": b["name"], "cust_served": b["customersServed"]})
                if b["customersOutNow"] > 0:
                    areas.append({"area_type": "county", "area_id": b["name"],
                                  "area_name": b["name"], "cust_out": b["customersOutNow"],
                                  "masked": 0, "cust_served": b["customersServed"],
                                  "n_out": "", "etr": ""})
    except (KeyError, TypeError) as e:
        raise SourceError(f"boundaries.json unexpected: {e!r}")

    return {"updated_ms": summary["source_updated_at"], "summary": summary,
            "areas": areas, "reference": ref, "incidents": incidents}
