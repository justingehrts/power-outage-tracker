import pandas as pd

import outage_data as od

IDX = od.county_index()
VALID = set(IDX["key"])


def T(s):
    return pd.Timestamp(s, tz="UTC")


def served(rows):
    return pd.DataFrame(rows, columns=["utility", "county", "cust_served"])


# ---- assets ----
def test_ohio_has_88_counties_and_every_zip_maps_to_one():
    assert len(IDX) == 88 and IDX["fips"].str.startswith("39").all()
    assert set(od.load_zip_to_county().values()) <= VALID
    assert od.load_zip_to_county()["43001"] == "LICKING"


def test_county_key_matches_all_spellings():
    assert od.county_key("HARDIN") == od.county_key("Hardin County") == "HARDIN"


# ---- which utility colours a county ----
def test_coop_with_a_real_share_beats_bigger_aep():
    g = served([("aep_ohio", "X", 7000), ("ure", "X", 3000)])      # co-op has 30%
    assert od.main_utility(g) == "ure"


def test_coop_with_a_small_share_does_not_beat_aep():
    g = served([("aep_ohio", "X", 9000), ("ure", "X", 1000)])      # co-op has 10%
    assert od.main_utility(g) == "aep_ohio"


def test_fringe_service_under_100_customers_does_not_colour_a_county():
    assert od.main_utility(served([("pioneer", "X", 50)])) is None
    # ...and does not make a co-op win over AEP either
    assert od.main_utility(served([("aep_ohio", "X", 120), ("pioneer", "X", 60)])) == "aep_ohio"


def test_territory_table_lists_every_utility_in_hover_and_marks_untracked_counties():
    sv = served([("aep_ohio", "UNION", 912), ("ure", "UNION", 7844)])
    t = od.territory_table(sv, IDX).set_index("key")
    assert t.loc["UNION", "main"] == "ure"
    assert "AEP Ohio: 912" in t.loc["UNION", "detail"] and "Union Rural Electric: 7,844" in t.loc["UNION", "detail"]
    assert pd.isna(t.loc["ASHTABULA", "main"])


# ---- customers served by county ----
def ref(rows):
    return pd.DataFrame(rows, columns=["utility", "area_type", "area_id", "area_name", "cust_served"])


def test_zip_only_utility_is_placed_by_zip_and_county_utility_is_not_double_counted():
    r = ref([("aep_ohio", "county", "c1", "Licking", 1000),
             ("aep_ohio", "zip", "43001", "43001", 999999),        # must be ignored for AEP
             ("ure", "zip", "43001", "43001", 40),
             ("ure", "zip", "43002", "43002", 60)])                # 43002 is Franklin County
    sv = od.served_by_county(r, od.load_zip_to_county(), VALID).set_index(["utility", "county"])["cust_served"]
    assert sv[("aep_ohio", "LICKING")] == 1000
    assert sv[("ure", "LICKING")] == 40 and sv[("ure", "FRANKLIN")] == 60


def test_non_ohio_counties_are_dropped():
    r = ref([("south_central_power", "county", "m", "MARSHALL", 500),
             ("south_central_power", "county", "f", "FAYETTE", 400)])   # Marshall is in West Virginia
    sv = od.served_by_county(r, {}, VALID)
    assert list(sv["county"]) == ["FAYETTE"]


# ---- outages by county ----
def summ(rows):
    return pd.DataFrame([{"utility": u, "polled_at": T(p), "ok": 1} for u, p in rows])


def areas(rows):
    return pd.DataFrame([{"utility": u, "polled_at": T(p), "area_type": t, "area_id": i,
                          "area_name": n, "cust_out": o} for u, p, t, i, n, o in rows])


def test_only_the_latest_poll_counts_and_a_clean_latest_poll_means_zero():
    r = ref([("aep_ohio", "county", "c", "Licking", 1000), ("ure", "zip", "43001", "43001", 40)])
    s = summ([("aep_ohio", "2026-11-07T10:00:00Z"), ("aep_ohio", "2026-11-07T11:00:00Z"),
              ("ure", "2026-11-07T10:00:00Z")])
    a = areas([("aep_ohio", "2026-11-07T10:00:00Z", "county", "c", "Licking", 500),   # old, restored
               ("ure", "2026-11-07T10:00:00Z", "zip", "43001", "43001", 7)])
    out = od.outages_by_county(a, s, r, od.load_zip_to_county(), VALID)
    got = out.set_index(["utility", "county"])["cust_out"].to_dict()
    assert ("aep_ohio", "LICKING") not in got            # AEP's latest poll had no outages
    assert got[("ure", "LICKING")] == 7


def test_percent_out_combines_utilities_and_leaves_untracked_counties_unknown():
    sv = served([("aep_ohio", "LICKING", 900), ("energy_coop", "LICKING", 100)])
    out = pd.DataFrame([("aep_ohio", "LICKING", 20), ("energy_coop", "LICKING", 30)],
                       columns=["utility", "county", "cust_out"])
    t = od.percent_out_table(sv, out, IDX).set_index("county")
    assert t.loc["Licking", "pct_out"] == 5.0 and t.loc["Licking", "cust_out"] == 50
    assert pd.isna(t.loc["Ashtabula", "pct_out"])         # unknown, not zero
