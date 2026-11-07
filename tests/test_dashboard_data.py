import pandas as pd
import pytest

import outage_data as od

NOW = pd.Timestamp("2026-11-07T12:00:00Z")


def T(s):
    return pd.Timestamp(s, tz="UTC")


def summary(rows):
    """rows: (polled_at, utility, cust_out, source_updated_at, ok)"""
    return pd.DataFrame([{
        "polled_at": T(p), "utility": u, "cust_out": o, "cust_served": 1000,
        "source_updated_at": T(s) if s else pd.NaT, "ok": ok, "outages": 1,
        "cust_out_exact": 1, "error": ""} for p, u, o, s, ok in rows])


def status(**fails):
    return pd.DataFrame([{"utility": u, "consecutive_failures": n, "last_success": pd.NaT,
                          "last_failure": pd.NaT, "last_error": ""} for u, n in fails.items()])


# ---- time series ----
def test_values_carry_forward_when_a_source_has_no_new_row():
    # B only has rows at 10:00 and 11:00; A updates every poll. B must not drop to zero at 10:30.
    s = summary([("2026-11-07T10:00:00Z", "a", 5, None, 1), ("2026-11-07T10:00:00Z", "b", 100, None, 1),
                 ("2026-11-07T10:30:00Z", "a", 7, None, 1),
                 ("2026-11-07T11:00:00Z", "a", 9, None, 1), ("2026-11-07T11:00:00Z", "b", 50, None, 1)])
    w = od.timeseries(s)
    assert w.loc[T("2026-11-07T10:30:00Z"), "b"] == 100


def test_failed_polls_are_ignored_not_counted_as_zero():
    s = summary([("2026-11-07T10:00:00Z", "a", 40, None, 1),
                 ("2026-11-07T10:15:00Z", "a", None, None, 0),
                 ("2026-11-07T10:30:00Z", "a", 42, None, 1)])
    w = od.timeseries(s)
    assert list(w["a"]) == [40, 42]


def test_clip_keeps_the_value_in_force_at_the_start():
    s = summary([("2026-11-07T08:00:00Z", "a", 30, None, 1), ("2026-11-07T11:00:00Z", "a", 10, None, 1)])
    w = od.clip(od.timeseries(s), T("2026-11-07T09:00:00Z"), NOW)
    assert w.iloc[0]["a"] == 30 and w.index[0] == T("2026-11-07T09:00:00Z")


def test_empty_summary_gives_empty_timeseries():
    assert od.timeseries(od.load_summary("/nonexistent", ["2026-11"])).empty


# ---- 24-hour peak ----
def test_peak_is_highest_total_in_last_24h_and_ignores_older():
    s = summary([("2026-11-05T12:00:00Z", "a", 9999, None, 1),      # old outage...
                 ("2026-11-05T13:00:00Z", "a", 0, None, 1),         # ...restored before the window
                 ("2026-11-06T18:00:00Z", "a", 100, None, 1), ("2026-11-06T18:00:00Z", "b", 50, None, 1),
                 ("2026-11-07T06:00:00Z", "a", 20, None, 1), ("2026-11-07T06:00:00Z", "b", 10, None, 1)])
    val, when = od.peak(od.timeseries(s), NOW)
    assert val == 150 and when == T("2026-11-06T18:00:00Z")


def test_peak_with_no_data():
    assert od.peak(pd.DataFrame(), NOW) == (0, None)


# ---- stale / failing ----
def test_stale_uses_longer_allowance_for_aep():
    # Both sources' own timestamps are 60 minutes old.
    s = summary([("2026-11-07T11:00:00Z", "aep_ohio", 5, "2026-11-07T11:00:00Z", 1),
                 ("2026-11-07T11:00:00Z", "ure", 5, "2026-11-07T11:00:00Z", 1)])
    latest = od.latest_by_utility(s, status(aep_ohio=0, ure=0), NOW)
    assert not latest.loc["aep_ohio", "stale"]
    assert latest.loc["ure", "stale"]


def test_aep_stale_beyond_90_minutes():
    s = summary([("2026-11-07T10:00:00Z", "aep_ohio", 5, "2026-11-07T10:00:00Z", 1)])
    assert od.latest_by_utility(s, status(aep_ohio=0), NOW).loc["aep_ohio", "stale"]


def test_failing_flag_and_source_with_no_good_reading_still_shown():
    s = summary([("2026-11-07T11:50:00Z", "ure", 5, "2026-11-07T11:50:00Z", 1)])
    latest = od.latest_by_utility(s, status(ure=2, logan_rec=1), NOW)
    assert latest.loc["ure", "failing"]
    assert "logan_rec" in latest.index and latest.loc["logan_rec", "failing"]


def test_percent_out():
    s = summary([("2026-11-07T11:50:00Z", "ure", 10, "2026-11-07T11:50:00Z", 1)])
    assert od.latest_by_utility(s, status(ure=0), NOW).loc["ure", "pct_out"] == pytest.approx(1.0)


# ---- loading ----
def test_months_between_crosses_year_boundary():
    assert od.months_between(T("2026-12-20T00:00:00Z"), T("2027-01-03T00:00:00Z")) == ["2026-12", "2027-01"]


def test_load_summary_from_local_folder_skips_missing_months_and_mixed_timestamps(tmp_path):
    (tmp_path / "summary").mkdir()
    (tmp_path / "summary" / "2026-11.csv").write_text(
        "polled_at,utility,source_updated_at,cust_out,cust_out_exact,cust_served,outages,ok,error\n"
        "2026-11-06T18:28:54Z,aep_ohio,2026-11-06T18:05:34.134031067Z,3032,1,1526520,39,1,\n"
        "2026-11-06T18:28:54Z,ure,2026-11-06T18:20:00Z,0,1,13460,0,1,\n")
    df = od.load_summary(tmp_path, ["2026-10", "2026-11"])   # October file does not exist
    assert len(df) == 2 and df["source_updated_at"].notna().all()
    assert str(df["polled_at"].dt.tz) == "UTC"
