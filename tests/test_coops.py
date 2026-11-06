import csv
import json
from pathlib import Path

import pytest

from collector import coop_a, nisc, run as runner
from collector.common import SourceError, iso_utc

FIX = Path(__file__).parent / "fixtures"


class Fake:
    """Serves canned JSON by URL suffix."""

    def __init__(self, files):
        self.files = files

    def get(self, url, timeout=30):
        data = next(v for k, v in self.files.items() if url.endswith(k))

        class R:
            def raise_for_status(self): pass

            def json(self): return data
        return R()


def nisc_session(fixture):
    return Fake({"summary.json": json.loads((FIX / fixture).read_text())})


# ---- time handling ----
def test_iso_utc_converts_offsets_and_milliseconds():
    assert iso_utc("2026-11-06T13:30:27.0738941-05:00") == "2026-11-06T18:30:27Z"
    assert iso_utc(1793986167000) == "2026-11-06T17:29:27Z"
    assert iso_utc(None) == ""


# ---- NISC ----
def test_nisc_incident_keeps_raw_xy_and_not_latlon():
    res = nisc.fetch(nisc_session("nisc_gmenergy.json"), "gmenergy", 17484)
    inc = res["incidents"][0]
    assert (inc["x_raw"], inc["y_raw"]) == (130633, 92130)
    assert "lat" not in inc
    assert res["summary"]["cust_out"] == 1


def test_nisc_uses_region_total_when_incident_list_is_empty():
    # Real case: Logan REC had a region with 1 customer out but no incident listed.
    res = nisc.fetch(nisc_session("nisc_loganrec_region_only.json"), "loganrec", 5019)
    assert res["incidents"] == []
    assert res["summary"]["cust_out"] == 1


def test_nisc_rejects_wrong_utility_by_served_count():
    with pytest.raises(SourceError, match="not within"):
        nisc.fetch(nisc_session("nisc_gmenergy.json"), "gmenergy", 5019)


def test_nisc_accepts_served_count_within_20_percent():
    nisc.fetch(nisc_session("nisc_gmenergy.json"), "gmenergy", 17484 * 1.15)


def test_nisc_registry_covers_all_cooperatives_and_not_lrec():
    names = {n for n, _ in nisc.COOPS.values()}
    assert len(names) == 7 and "lrec" not in names


# ---- Platform A ----
def coop_a_session(out_now=3, served=128549, boundaries=None, outages=None):
    return Fake({
        "outageSummary.json": {"customersOutNow": out_now, "customersServed": served,
                               "updateTime": "2026-11-06T13:30:27.0738941-05:00"},
        "boundaries.json": boundaries if boundaries is not None else [],
        "outages.json": outages if outages is not None else
        json.loads((FIX / "southcentral_outages.json").read_text()),
    })


def test_coop_a_parses_real_incident_sample():
    res = coop_a.fetch(coop_a_session(), "https://x")
    assert res["summary"]["source_updated_at"] == "2026-11-06T18:30:27Z"
    assert len(res["incidents"]) == res["summary"]["outages"] == 3
    i = res["incidents"][0]
    assert 38 < i["lat"] < 42 and -85 < i["lon"] < -80   # inside Ohio
    assert i["start_time"].endswith("Z")


def test_coop_a_empty_boundaries_and_outages_is_normal():
    res = coop_a.fetch(coop_a_session(out_now=0, outages=[]), "https://x")
    assert res["areas"] == [] and res["incidents"] == []


def test_coop_a_counties_only_nonzero_in_areas():
    b = [{"name": "Counties", "boundaries": [
        {"name": "Fayette", "customersOutNow": 0, "customersServed": 426},
        {"name": "Pickaway", "customersOutNow": 2, "customersServed": 900}]}]
    res = coop_a.fetch(coop_a_session(boundaries=b), "https://x")
    assert [a["area_name"] for a in res["areas"]] == ["Pickaway"]
    assert len(res["reference"]) == 2


def test_coop_a_bad_format_fails_loudly():
    with pytest.raises(SourceError):
        coop_a.fetch(Fake({"outageSummary.json": {"oops": 1}}), "https://x")


# ---- Runner: reference refreshed per utility ----
def test_reference_table_keeps_every_utility(tmp_path):
    def src(name):
        def f(_):
            return {"updated_ms": 1, "areas": [], "reference": [
                {"area_type": "county", "area_id": name, "area_name": name, "cust_served": 5}],
                "summary": {"source_updated_at": "t", "cust_out": 0, "cust_out_exact": 1,
                            "cust_served": 5, "outages": 0}}
        return f

    runner.run(tmp_path, sources={"a": src("A"), "b": src("B")})
    rows = list(csv.DictReader(open(tmp_path / "reference" / "areas.csv")))
    assert {r["utility"] for r in rows} == {"a", "b"}   # second source was not skipped
