# Central Ohio Power Outage Tracker

Polls Central Ohio utility outage maps every 15 minutes, stores the results as CSV on the
`data` branch, and (later) shows them in a Streamlit dashboard. See `CLAUDE.md` for the full design.

## Status
Phases 1-2: AEP Ohio, South Central Power, Consolidated Electric and 7 NISC-hosted co-ops (10 sources). Phase 3: dashboard v1 (Now, time series, downloads, source status). Maps and the AEP incident crawl come next.

## Run locally
```bash
pip install -r requirements.txt
python -m collector.run --data-dir data   # writes CSVs into ./data (git-ignored)
python -m pytest
```

## Dashboard
```bash
streamlit run app/streamlit_app.py                          # reads the live `data` branch
OUTAGE_DATA_SOURCE=./data streamlit run app/streamlit_app.py  # or a local data folder
```
To put it online (free): sign in at https://share.streamlit.io with GitHub, click **Create app**, pick this
repo, branch `main`, main file path `app/streamlit_app.py`. It redeploys whenever `main` changes
(which is why collected data lives on the `data` branch, not `main`).

- A source is flagged **stale** if its own timestamp is over 45 min old (90 min for AEP, which can go
  almost an hour between refreshes).
- The chart carries each utility's last value forward until its next reading and marks real readings with
  dots, so gaps in collection are visible.

## Map
The **Map** tab has two views: *Service territories* (which utility serves which county, with a "highlight one
utility" picker) and *Customers out now* (% of customers out per county, all utilities combined).

- Territories come from each utility's own area list in `reference/areas.csv`. Utilities that report only by ZIP
  code (Union Rural Electric, Logan County Electric) are placed through a ZIP-to-county table, so their edges are
  approximate. Consolidated Electric publishes no county data, so it is not drawn yet.
- A co-op is shown wherever it has at least 20% of the tracked customers in a county (otherwise AEP would cover
  it), and a utility with under 100 customers in a county is treated as fringe service and does not colour it.
- Only tracked utilities appear. Grey counties are not covered yet (AES Ohio, FirstEnergy, Columbus, municipals).
- The two small map files in `app/assets/` (Ohio county outlines and ZIP-to-county) come from the US Census.
  Rebuild them with `python tools/build_map_assets.py`; they rarely need to change.

## Important notes
- **Data never goes on `main`.** The GitHub Action commits CSVs to the `data` branch only.
- **Public repo = free Actions minutes.** Private repos have a monthly quota that 96 runs/day may exceed.
- **Inactivity:** GitHub disables scheduled workflows in public repos after 60 days without repo
  activity. If collection stops, open the Actions tab and re-enable it (or push any commit).
- **Gaps are normal.** GitHub cron can run late or skip runs. Every row has `polled_at`; never assume a regular interval.
- **Masked values:** AEP reports small counts as "fewer than 5". These are stored with `masked=1`.
- Unofficial endpoints can change without notice. A source failing 3 runs in a row fails the workflow
  (you get a GitHub email) and shows in `status.csv`.
