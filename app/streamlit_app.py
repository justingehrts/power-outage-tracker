"""Central Ohio Power Outage dashboard (v1: Now, time series, downloads, source status)."""
import os
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import outage_data as od

LOCAL_TZ = "America/New_York"   # Columbus
SOURCE = os.environ.get("OUTAGE_DATA_SOURCE", od.DEFAULT_SOURCE)

st.set_page_config(page_title="Central Ohio Power Outages", layout="wide")

# Big, high-contrast numbers for TV-style readability
st.markdown("""
<style>
.big { font-size: 5rem; font-weight: 800; line-height: 1; }
.card { border: 2px solid #888; border-radius: 12px; padding: 14px 18px; margin-bottom: 14px; }
.card.stale { border-color: #ffb000; }
.card.failing { border-color: #ff4d4d; }
.card .num { font-size: 2.6rem; font-weight: 800; line-height: 1.1; }
.card .small { opacity: .85; font-size: .95rem; }
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=300, show_spinner="Loading outage data...")
def load(months, source):
    return od.load_summary(source, list(months)), od.load_status(source)


def local(ts):
    return ts.tz_convert(LOCAL_TZ)


def fmt_time(ts):
    return "never" if pd.isna(ts) else local(ts).strftime("%a %b %-d, %-I:%M %p")


st.title("Central Ohio Power Outages")

# ---- Sidebar: time range ----
now = od.utc_now()
choice = st.sidebar.radio("Time range", ["Last 24 hours", "Last 7 days", "Custom"])
if choice == "Last 24 hours":
    start = now - timedelta(hours=24)
elif choice == "Last 7 days":
    start = now - timedelta(days=7)
else:
    d0, d1 = st.sidebar.date_input("Dates", (date.today() - timedelta(days=3), date.today()))
    start = pd.Timestamp(d0, tz=LOCAL_TZ).tz_convert("UTC")
    now_end = pd.Timestamp(d1, tz=LOCAL_TZ).tz_convert("UTC") + timedelta(days=1)
end = now if choice != "Custom" else min(now, now_end)
st.sidebar.caption("Times are shown in Eastern. Data refreshes about every 5 minutes.")
if st.sidebar.button("Refresh now"):
    st.cache_data.clear()

# Always include the last 7 days so "Now" and the 24-hour peak work for any selected range.
months = tuple(od.months_between(min(start, now - timedelta(days=7)), now))
summary, status = load(months, SOURCE)

if summary.empty:
    st.warning("No data found yet. The collector may not have run, or the data source is "
               f"unreachable ({SOURCE}).")
    st.stop()

latest = od.latest_by_utility(summary, status, now)
wide = od.timeseries(summary)

tab_now, tab_series, tab_status = st.tabs(["Now", "Time series", "Source status"])

# ---------------- Now ----------------
with tab_now:
    total_out = int(latest["cust_out"].fillna(0).sum())
    total_served = int(latest["cust_served"].fillna(0).sum())
    peak_val, peak_time = od.peak(wide, now)
    left, right = st.columns([2, 1])
    left.markdown(f"<div class='big'>{total_out:,}</div>customers without power right now "
                  f"(of {total_served:,} served by tracked utilities)", unsafe_allow_html=True)
    right.markdown(f"<div class='card'><div class='small'>24-hour maximum</div>"
                   f"<div class='num'>{peak_val:,}</div>"
                   f"<div class='small'>{fmt_time(peak_time) if peak_time is not None else '-'}"
                   f"</div></div>", unsafe_allow_html=True)

    n_stale = int(latest["stale"].sum())
    n_fail = int(latest["failing"].sum())
    if n_stale == len(latest) and n_stale > 1:
        newest = latest["source_updated_at"].max()
        st.warning(f"No source has reported new data since {fmt_time(newest)}. The collector may be "
                   "delayed (GitHub can run scheduled jobs late). The numbers above are the last "
                   "ones recorded, not live.")
    elif n_stale:
        names = ", ".join(od.display_name(u) for u in latest.index[latest["stale"]])
        st.warning(f"Out-of-date data from: {names}. The total above uses their last known value.")
    if n_fail:
        names = ", ".join(od.display_name(u) for u in latest.index[latest["failing"]])
        st.error(f"Collector is failing for: {names}. See the Source status tab.")

    st.write("")
    cols = st.columns(3)
    order = latest.sort_values("cust_out", ascending=False, na_position="last")
    for i, (u, r) in enumerate(order.iterrows()):
        cls = "failing" if r["failing"] else ("stale" if r["stale"] else "")
        out = "n/a" if pd.isna(r["cust_out"]) else f"{int(r['cust_out']):,}"
        pct = "" if pd.isna(r["pct_out"]) else f"{r['pct_out']:.2f}% out of {int(r['cust_served']):,}"
        upd = "no data" if pd.isna(r["source_updated_at"]) else f"updated {fmt_time(r['source_updated_at'])}"
        flag = " (STALE)" if r["stale"] and not r["failing"] else ""
        cols[i % 3].markdown(
            f"<div class='card {cls}'><div class='small'><b>{od.display_name(u)}</b>{flag}</div>"
            f"<div class='num'>{out}</div><div class='small'>{pct}<br>{upd}</div></div>",
            unsafe_allow_html=True)

# ---------------- Time series ----------------
with tab_series:
    window = od.clip(wide, start, end)
    if window.empty:
        st.info("No readings in this time range.")
    else:
        names = {u: od.display_name(u) for u in window.columns}
        shown = st.multiselect("Utilities", options=list(window.columns), default=list(window.columns),
                               format_func=lambda u: names[u])
        window = window[shown] if shown else window.iloc[:, :0]
        view = window.copy()
        view.index = view.index.tz_convert(LOCAL_TZ)
        long = view.rename(columns=names).reset_index(names="time").melt(
            id_vars="time", var_name="Utility", value_name="Customers out")
        fig = px.area(long, x="time", y="Customers out", color="Utility", line_shape="hv")
        # Dots at the moments readings were actually recorded, so sparse data looks sparse
        real = view.iloc[1:] if window.index[0] == start else view
        if not real.empty:
            fig.add_trace(go.Scatter(x=real.index, y=real.sum(axis=1), mode="markers",
                                     name="Recorded readings (total)",
                                     marker=dict(size=8, color="white", line=dict(width=1, color="black"))))
        fig.update_layout(xaxis_title=None, legend_title=None, height=480,
                          legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig, width="stretch")
        st.caption("Lines are drawn as steps. The collector only records a new row when a utility "
                   "updates its numbers, so the last value holds until the next one. White dots mark "
                   "when readings were actually recorded; long stretches without dots mean "
                   "no new data arrived.")

        # Downloads: the filtered view, as recorded (UTC, one row per utility reading)
        rows = summary[(summary["polled_at"] >= start) & (summary["polled_at"] <= end)
                       & (summary["utility"].isin(shown))]
        d1, d2 = st.columns(2)
        d1.download_button("Download readings (CSV)", rows.to_csv(index=False).encode(),
                           "outage_readings.csv", "text/csv")
        d2.download_button("Download chart table (CSV)", window.to_csv().encode(),
                           "outage_timeseries.csv", "text/csv")

# ---------------- Source status ----------------
with tab_status:
    if status.empty:
        st.info("No status file yet.")
    else:
        t = status.copy()
        t["utility"] = t["utility"].map(od.display_name)
        t["last_error"] = t["last_error"].fillna("")
        for c in ("last_success", "last_failure"):
            t[c] = t[c].apply(fmt_time).replace("never", "")
        st.dataframe(t.rename(columns={
            "utility": "Source", "last_success": "Last success", "last_failure": "Last failure",
            "consecutive_failures": "Failures in a row", "last_error": "Last error"}),
            hide_index=True, width="stretch")
    st.caption("A source is flagged stale when its own timestamp is over 45 minutes old "
               "(90 for AEP Ohio, which sometimes refreshes slowly).")
