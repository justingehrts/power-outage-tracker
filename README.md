# Central Ohio Power Outage Tracker

Polls Central Ohio utility outage maps every 15 minutes, stores the results as CSV on the
`data` branch, and (later) shows them in a Streamlit dashboard. See `CLAUDE.md` for the full design.

## Status
Phases 1-2: AEP Ohio, South Central Power, Consolidated Electric and 7 NISC-hosted co-ops (10 sources). The dashboard comes next.

## Run locally
```bash
pip install -r requirements.txt
python -m collector.run --data-dir data   # writes CSVs into ./data (git-ignored)
python -m pytest
```

## Important notes
- **Data never goes on `main`.** The GitHub Action commits CSVs to the `data` branch only.
- **Public repo = free Actions minutes.** Private repos have a monthly quota that 96 runs/day may exceed.
- **Inactivity:** GitHub disables scheduled workflows in public repos after 60 days without repo
  activity. If collection stops, open the Actions tab and re-enable it (or push any commit).
- **Gaps are normal.** GitHub cron can run late or skip runs. Every row has `polled_at`; never assume a regular interval.
- **Masked values:** AEP reports small counts as "fewer than 5". These are stored with `masked=1`.
- Unofficial endpoints can change without notice. A source failing 3 runs in a row fails the workflow
  (you get a GitHub email) and shows in `status.csv`.
