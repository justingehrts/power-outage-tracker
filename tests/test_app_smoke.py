"""Loads the real Streamlit page against a small fake data folder and checks it renders."""
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest


APP = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")


def make_data(root, minutes_old):
    now = pd.Timestamp.now(tz="UTC")
    when = (now - pd.Timedelta(minutes=minutes_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (root / "summary").mkdir()
    (root / "summary" / f"{now:%Y-%m}.csv").write_text(
        "polled_at,utility,source_updated_at,cust_out,cust_out_exact,cust_served,outages,ok,error\n"
        f"{when},aep_ohio,{when},120,1,1500000,4,1,\n"
        f"{when},ure,{when},30,1,13000,1,1,\n")
    (root / "status.csv").write_text(
        "utility,last_success,last_failure,consecutive_failures,last_error,last_updated_ms\n"
        f"aep_ohio,{when},,0,,1\nure,{when},,0,,1\n")


def run_app(monkeypatch, tmp_path):
    monkeypatch.setenv("OUTAGE_DATA_SOURCE", str(tmp_path))
    return AppTest.from_file(APP, default_timeout=60).run()


def test_page_renders_totals_without_warnings_for_fresh_data(monkeypatch, tmp_path):
    make_data(tmp_path, minutes_old=5)
    at = run_app(monkeypatch, tmp_path)
    assert not at.exception
    assert not at.warning and not at.error
    assert any(">150<" in m.value for m in at.markdown)     # 120 + 30 customers out


def test_page_warns_when_all_data_is_old(monkeypatch, tmp_path):
    make_data(tmp_path, minutes_old=300)
    at = run_app(monkeypatch, tmp_path)
    assert not at.exception
    assert any("No source has reported new data" in w.value for w in at.warning)


def test_page_handles_no_data(monkeypatch, tmp_path):
    at = run_app(monkeypatch, tmp_path)
    assert not at.exception
    assert any("No data found" in w.value for w in at.warning)
