import csv

import pytest

from collector import aep, run as runner


class FakeSession:
    """Serves canned JSON by URL substring, like the real Kubra endpoints."""

    def __init__(self, county_areas, zip_areas, total):
        self.total = total
        self.county_areas, self.zip_areas = county_areas, zip_areas

    def get(self, url, timeout=30):
        outer = self

        class R:
            def raise_for_status(self): pass

            def json(self):
                if "currentState" in url:
                    return {"updatedAt": 1, "stormcenterDeploymentId": "dep",
                            "data": {"interval_generation_data": "data/snap"}}
                if "configuration" in url:
                    return {"config": {"reports": {"data": {"interval_generation_data": [
                        {"areaType": "county", "source": "public/reports/c.json"},
                        {"areaType": "zip", "source": "public/reports/z.json"}]}}}}
                if "summary-1" in url:
                    return {"summaryFileData": {"date_generated": "2026-01-01T00:00:00Z",
                            "totals": [{"total_cust_a": {"val": outer.total},
                                        "total_cust_s": 1000, "total_outages": 2}]}}
                if "c.json" in url:
                    return {"file_data": {"areas": outer.county_areas}}
                return {"file_data": {"areas": outer.zip_areas}}
        return R()


def area(name, out, mask=None, served=100):
    c = {"val": out}
    if mask:
        c["mask"] = mask
    return {"areaId": f"Boun|OH|{name}|county", "name": name, "cust_a": c,
            "cust_s": served, "n_out": 1, "etr": ""}


def test_masked_flag_and_zero_areas_skipped():
    s = FakeSession([area("A", 4, mask=5), area("B", 0), area("C", 20)], [area("Z", 3)], 24)
    res = aep.fetch(s)
    by = {a["area_name"]: a for a in res["areas"] if a["area_type"] == "county"}
    assert by["A"]["masked"] == 1 and by["A"]["cust_out"] == 4
    assert by["C"]["masked"] == 0
    assert "B" not in by                      # zero-outage areas not stored
    assert len(res["reference"]) == 4         # but all areas are in the reference table


def test_county_sum_matches_summary_when_nothing_masked():
    s = FakeSession([area("A", 5), area("C", 20)], [area("Z", 25)], 25)
    res = aep.fetch(s)
    county_sum = sum(a["cust_out"] for a in res["areas"] if a["area_type"] == "county")
    assert county_sum == res["summary"]["cust_out"]


def test_county_sum_close_to_summary_when_masked():
    # masked values are caps (<5), so the sum may overshoot slightly but not wildly
    s = FakeSession([area("A", 5, mask=5), area("C", 20)], [area("Z", 25)], 22)
    res = aep.fetch(s)
    county_sum = sum(a["cust_out"] for a in res["areas"] if a["area_type"] == "county")
    assert abs(county_sum - res["summary"]["cust_out"]) <= 5 * sum(
        a["masked"] for a in res["areas"] if a["area_type"] == "county")


def test_empty_report_fails_loudly_instead_of_writing_zeros():
    with pytest.raises(aep.SourceError):
        aep.fetch(FakeSession([], [area("Z", 1)], 1))


def test_failure_is_isolated_and_counted(tmp_path):
    def boom(_):
        raise aep.SourceError("down")

    def ok(_):
        return {"updated_ms": 5, "areas": [], "reference": [],
                "summary": {"source_updated_at": "t", "cust_out": 0, "cust_out_exact": 1,
                            "cust_served": 10, "outages": 0}}

    for _ in range(3):
        code = runner.run(tmp_path, sources={"bad": boom, "good": ok})
    status = {r["utility"]: r for r in csv.DictReader(open(tmp_path / "status.csv"))}
    assert status["bad"]["consecutive_failures"] == "3"
    assert status["good"]["consecutive_failures"] == "0"
    assert code == 1                           # 3 in a row -> workflow fails visibly
