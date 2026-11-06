"""NISC hosted outage maps (most Central Ohio co-ops)."""
from collector.common import SourceError, get_json, iso_utc

URL = "https://outagemap-data.cloud.coop/{name}/Hosted_Outage_Map/summary.json"

# utility key -> (NISC name, customers served when last verified on 2026-11-03)
COOPS = {
    "ure": ("ure", 13460),
    "energy_coop": ("theenergycoop", 28144),
    "logan_rec": ("loganrec", 5019),
    "frontier_power": ("frontierpower", 9377),
    "gm_energy": ("gmenergy", 17484),
    "pioneer": ("pioneerec", 18010),
    "buckeye_rec": ("buckeyerec", 18934),
}

# Region dataset names vary by co-op ("Zip Code", "Townships", "Counties" ...)
AREA_TYPES = {"zip code": "zip", "township": "township", "townships": "township",
              "county": "county", "counties": "county", "substation": "substation"}

TOLERANCE = 0.20  # totalServed must stay within +/-20% of the verified value


def fetch(session, name, expected_served):
    d = get_json(session, URL.format(name=name))
    try:
        served = d["totalServed"]
        incidents_raw = d["outages"]
        datasets = d["regionDataSets"]
        updated = iso_utc(d["lastUpdate"])
    except (KeyError, TypeError, ValueError) as e:
        raise SourceError(f"{name} summary unexpected: {e!r}")

    # Catches a wrong-utility mixup or a changed endpoint.
    if not served or abs(served - expected_served) > TOLERANCE * expected_served:
        raise SourceError(f"{name}: totalServed {served} is not within "
                          f"{int(TOLERANCE * 100)}% of expected {expected_served}")

    incidents = []
    try:
        for o in incidents_raw:
            incidents.append({
                "incident_id": o["id"], "x_raw": o["x"], "y_raw": o["y"],  # not lat/lon!
                "cust_out": o["nbrOut"], "masked": 0, "n_out": 1,
                "start_time": iso_utc(o.get("timeOff")),
                "planned": int(bool(o.get("planned"))),
            })
        areas, ref = [], []
        for ds in datasets:
            atype = AREA_TYPES.get(ds["description"].strip().lower())
            if atype is None:
                continue  # unknown dataset type: skip rather than guess
            for r in ds["regions"]:
                ref.append({"area_type": atype, "area_id": str(r["id"]),
                            "area_name": str(r["id"]), "cust_served": r["numberServed"]})
                if r["numberOut"] > 0:
                    areas.append({"area_type": atype, "area_id": str(r["id"]),
                                  "area_name": str(r["id"]), "cust_out": r["numberOut"],
                                  "masked": 0, "cust_served": r["numberServed"],
                                  "n_out": "", "etr": ""})
    except (KeyError, TypeError, AttributeError) as e:
        raise SourceError(f"{name} summary unexpected: {e!r}")

    # Incidents and region datasets can disagree (Logan REC showed a region with 1 customer
    # out and no incident), so take the largest total rather than trusting one list.
    region_totals = {}
    for r in areas:
        region_totals[r["area_type"]] = region_totals.get(r["area_type"], 0) + r["cust_out"]
    cust_out = max([sum(i["cust_out"] for i in incidents), *region_totals.values()])

    summary = {"source_updated_at": updated,
               "cust_out": cust_out,
               "cust_out_exact": 1, "cust_served": served, "outages": len(incidents)}
    return {"updated_ms": d["lastUpdate"], "summary": summary, "areas": areas,
            "reference": ref, "incidents": incidents}
