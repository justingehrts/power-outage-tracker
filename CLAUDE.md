# Central Ohio Power Outage Tracker

Logs power outage data from Central Ohio utilities every 15 minutes to CSV, and shows it in a Streamlit dashboard (current status, time series, maps).

Owner: Justin, a broadcast/digital meteorologist in the Columbus, Ohio TV market. He is new to Git and Claude Code workflows, so explain steps plainly and keep changes small and reviewable.

## Architecture

```
GitHub Actions (cron, every 15 min)
  └─ collector/  → polls each utility → appends CSVs → commits to `data` branch
Streamlit Community Cloud (deploys from `main`)
  └─ app/        → reads CSVs from the `data` branch (raw.githubusercontent.com) → dashboard
```

- **Never commit collected data to `main`.** Streamlit redeploys on every push to `main`. Data goes to a separate `data` branch only.
- The dashboard reads CSVs over HTTP from the `data` branch and caches them (`st.cache_data`, ttl about 5 min).
- Python 3.11+, `requests`, `pandas`, `polyline`, `streamlit`, `plotly` (or `pydeck`). No database: CSV is the store.

### Things to check early (Phase 1)
- **Runner IP blocking:** confirm every source responds from a GitHub Actions runner (Azure IPs). If one blocks, flag it; don't try to evade blocking.
- **Actions minutes:** public repos get free Actions minutes; private repos have a monthly quota that 96 runs a day may exceed. Check this and tell Justin before choosing public or private.
- **Cron reliability:** GitHub cron can run late or skip runs under load. The data must tolerate gaps (always store `polled_at`; never assume a regular interval).
- **Inactivity:** GitHub may disable scheduled workflows in public repos after long inactivity. Note this in the README.

## Polling etiquette (required)
- Send a descriptive User-Agent with a contact, e.g. `central-ohio-outage-tracker (contact: <email>)`.
- Poll no more often than every 15 minutes (sources refresh about every 15 minutes).
- Retry with backoff, then give up for that cycle. Never hammer a failing source.
- Each source must be isolated: one source failing must not stop the others.
- Log per-source success or failure each run. If a source fails 3 runs in a row, make it visible (failed workflow, or a `status.csv` the dashboard shows).
- These are unofficial, undocumented endpoints. Expect them to change. Parse defensively, and fail loudly instead of writing zeros.

---

## Source 1: AEP Ohio (Ohio Power Company), Kubra Storm Center

The largest utility in the market (about 1.53M customers statewide). Verified working 2026-11-03.

Instance `9c0735d8-b721-4dce-b80b-558e98ce1083`, view `9b2feb80-69f8-4035-925e-f2acbcf1728e`.

```
BASE  = https://kubra.io
STATE = {BASE}/stormcenter/api/v1/stormcenters/{instance}/views/{view}/currentState?preview=false
CONFIG= {BASE}/stormcenter/api/v1/stormcenters/{instance}/views/{view}/configuration/{stormcenterDeploymentId}?preview=false
```

### Poll sequence
1. GET `STATE`. Read:
   - `data.interval_generation_data`, e.g. `data/0e382012-…`. This is the snapshot path, and it **changes every refresh** (about 15 min).
   - `data.cluster_interval_generation_data`, the tile path template containing `{qkh}`.
   - `stormcenterDeploymentId`, needed for CONFIG.
   - `updatedAt`, in Unix milliseconds.
2. If `updatedAt` equals the last stored value, skip: no new data.
3. GET `CONFIG`. Cache it and re-fetch only when `stormcenterDeploymentId` changes. Read report and layer definitions from it at runtime; **do not hardcode the report IDs** (they're listed below only for reference).
4. Summary: `{BASE}/{snapshot}/public/summary-1/data.json`
   - `summaryFileData.totals[0]`: `total_cust_a.val` (customers out, **exact**), `total_cust_s` (served), `total_outages`.
   - `summaryFileData.date_generated` (ISO UTC).
5. Reports, from `config.reports.data.interval_generation_data[]`: each has `source` (e.g. `public/reports/<id>_report.json`) and `areaType`.
   - URL: `{BASE}/{snapshot}/{source}`
   - Area types and report IDs as of 2026-11-03:
     - `county`: `1bc6bd19-2315-4548-980a-6df73b93b355` (60 counties)
     - `zip`: `6cac6f94-e827-484c-8fbc-312a62ca2fe4` (about 150 KB)
     - `district`: `ea233558-d247-46f4-961b-765374c2a0c3` (e.g. COLUMBUS)
     - `operatingarea`: `1cdf1d6c-19ec-49be-85e6-e479b269643a`
   - `file_data.areas[]`: `name`, `cust_a.val`, `cust_a.mask` (optional), `cust_s`, `n_out`, `percent_cust_a.val`, `etr`, `etr_confidence`, `areaId` (e.g. `Boun|OH|Franklin|county`), `gotoMap.bbox` ([W,S,E,N]), `state`.
   - Reports list **every** area, including zero-outage ones.
6. Incident points (tiles):
   - Layer `cluster-1`, source `public/cluster-1/{q}.json` (from the config).
   - URL: `{BASE}/{cluster_interval_generation_data with {qkh} replaced}/public/cluster-1/{quadkey}.json`
   - `quadkey`: standard Bing tile quadkey. **`qkh = quadkey[-3:][::-1]`** (last 3 digits, reversed). Verified.
   - Responses are **gzip-compressed** (magic bytes `1f 8b`). 404 or 403 means an empty tile.
   - `file_data[]`: `id`, `desc.inc_id`, `desc.cust_a` (val, optional mask), `desc.n_out`, `desc.cluster` (bool), `desc.crew_status` (`EN-US`, `orig`), `desc.etr`, `desc.start_time`, `desc.cause`, `geom.p[0]` (Google encoded polyline, precision 5 → one (lat, lon)).
   - **Crawl rule (important):** start with zoom-7 tiles covering the union of county-report bboxes. Low-zoom tiles **drop some unclustered points**, so descending only into tiles containing clusters undercounts (verified: 12 found vs 14 actual). Descend into all 4 children of **every non-empty tile**, up to zoom 14 (map max is 16). **Stop early** once the sum of `n_out` over unique incidents equals the summary's `total_outages`. Dedupe by `inc_id`, falling back to the coordinates.
   - Quiet night: about 230 requests and 1 minute. In big storms this grows. Cap requests per run (e.g. 2,000). If capped, log county/ZIP data only, and record that incidents were incomplete.
7. Optional geometry: `public/thematic-1/thematic_areas.json` (county), `thematic-2` (district), `thematic-3` (operating area), `thematic-4` (ZIP). `file_data[].geom.a` is a list of encoded polyline rings (precision 5) and `geom.p` is a label point. These only include areas with outages. For mapping, prefer Census boundary files joined on county name or ZIP.

### AEP quirks
- **Masking:** small counts appear as `{"val": 4, "mask": 5}`, meaning "fewer than 5", i.e. 1–4. Never treat `val` as exact when `mask` is present. Store `masked=1`.
- The summary total is exact, so the masked incidents' combined total = summary `total_cust_a` − sum of unmasked incident `cust_a`. This works only territory-wide, not per county.
- `cust_s` is AEP's customers in that area only, not every customer there.

---

## Source 2: Co-op platform A (South Central Power, Consolidated Electric)

Plain JSON, no snapshot chasing. Vendor not identified.

| Utility | Base |
|---|---|
| South Central Power | `https://outage.southcentralpower.com` |
| Consolidated Electric | `https://outagewatch.consolidated.coop` |

- `GET {base}/data/outageSummary.json`: `customersOutNow`, `customersAffected`, `customersRestored`, `customersServed`, `updateTime` (ISO with offset), and `hourlyCustomersOut[]` (`customers`, `eventTime`): **49 hourly points (about 48 h)**. Use these to backfill gaps after missed polls.
- `GET {base}/data/boundaries.json`: `[{name: "Counties", boundaries: [{name, customersOutNow, customersAffected, customersServed}]}]`. Consolidated's was empty when sampled.
- `GET {base}/data/outages.json` and `/data/outagePolygons.json`: incident data. **Both were empty when sampled (`[]`), so the format is unknown.** Log raw JSON to `raw_samples/` the first time they're non-empty, then write the parser.

---

## Source 3: NISC hosted outage maps (most co-ops)

```
https://outagemap-data.cloud.coop/{name}/Hosted_Outage_Map/summary.json
https://outagemap-data.cloud.coop/{name}/Hosted_Outage_Map/config.json   (branding, map settings)
```

| PUCO name | `{name}` | Region dataset | Served (2026-11-03) |
|---|---|---|---|
| Union Rural Electric | `ure` | ZIP | 13,460 |
| Licking Rural Electrification (The Energy Cooperative) | `theenergycoop` | Substation | 28,144 |
| Logan County Electric | `loganrec` | ZIP | 5,019 |
| Frontier Power | `frontierpower` | Township | 9,377 |
| Guernsey-Muskingum Electric | `gmenergy` | ZIP | 17,484 |
| Pioneer Electric | `pioneerec` | ZIP | 18,010 |
| Buckeye Rural Electric | `buckeyerec` | County | 18,934 |

Optional (western Ohio): `darkerec`, `midwestrec`. **Do not use `lrec`**: that is Lake Region Electric, in another state.

- `summary.json`: `totalServed`; `outages[]` with `id`, `nbrOut`, `timeOff` (Unix ms), `planned` (bool), `x`, `y`; `regionDataSets[]` with `id`, `description`, and `regions[]` (`id`, `numberOut`, `numberServed`).
- **`x`/`y` are not lat/lon** (e.g. `x: 126537, y: 37311`), and the coordinate system is unknown. Investigate: check `config.json` for a spatial reference or offset, and compare against where the live map draws the point. Until it's solved, store raw `x`/`y` and skip mapping NISC points.
- No history is provided. Build the time series from polls.
- These hosts don't block scripts, but the co-op websites themselves may (e.g. ure.com returns 403).

---

## Known gaps (not yet sourced)

| Utility | Status |
|---|---|
| AES Ohio (Madison, Fayette, Champaign) | Map is loaded by JavaScript. Check browser DevTools for a Kubra `currentState`; if found, reuse the AEP code with new instance and view IDs. |
| Ohio Edison / FirstEnergy (Marion, Morrow, Knox edges) | Map returned 503 to scripts. Needs a browser check. |
| City of Columbus Division of Power | Site returns 403 to scripts. Needs a browser check; may need Playwright, or may be impossible. |
| Mid-Ohio Energy | No public outage map found. |
| Municipal systems (e.g. Westerville) | Not on the PUCO territory map. Check AMP's member list. |
| DOE EAGLE-I | Live system is restricted to government partners; public data is yearly historical only (good for backfill and baselines). The FEMA-hosted EAGLE-I ArcGIS service returned 503 (Cloudflare) when tested. |

---

## CSV layout (on the `data` branch)

All times are ISO 8601 UTC. Monthly files keep sizes manageable.

`data/summary/YYYY-MM.csv`: one row per utility per poll
```
polled_at, utility, source_updated_at, cust_out, cust_out_exact, cust_served, outages, ok, error
```

`data/areas/YYYY-MM.csv`: **only areas with outages > 0** (zeros are implied by the reference table)
```
polled_at, utility, area_type, area_id, area_name, cust_out, masked, cust_served, n_out, etr
```

`data/incidents/YYYY-MM.csv`: one row per incident per poll
```
polled_at, utility, incident_id, lat, lon, x_raw, y_raw, cust_out, masked, n_out, crew_status, cause, start_time, etr, planned
```

`data/reference/areas.csv`: every area's customers served, refreshed daily (overwrite)
```
utility, area_type, area_id, area_name, cust_served, updated_at
```

`data/status.csv`: last success and failure per source, consecutive failure count (overwrite).

Utility keys: `aep_ohio`, `south_central_power`, `consolidated`, `ure`, `energy_coop`, `logan_rec`, `frontier_power`, `gm_energy`, `pioneer`, `buckeye_rec`.

Masked values: store the raw `cust_out` (e.g. 4) and `masked=1`. Let the dashboard decide how to show them (e.g. "<5"), never summing them as exact.

---

## Dashboard (Streamlit, `app/`)

1. **Now:** total customers out across all sources; per-utility cards (out, served, % out, last update); a stale-data warning if a source is more than 45 min old.
2. **Time series:** customers out over time (stacked by utility, or total), selectable range (24 h / 7 d / custom), plus the 24-hour peak and its time (like the NBC5 "24-Hour Maximum").
3. **County map:** choropleth of % out by county (Census county boundaries for Ohio, joined on name). Combine utilities per county where a county is served by several.
4. **Incident map:** AEP incident points (later, NISC points once `x`/`y` is solved), sized by customers out.
5. **Downloads:** CSV export of the filtered view.
6. **Source status:** a table built from `status.csv`.

Design for TV readability: large numbers, high contrast, dark theme option.

---

## Build phases

1. **Collector (AEP summary + county + ZIP reports)** → CSV, run locally, then as a GitHub Action writing to the `data` branch. Check runner access and Actions minutes.
2. **Add the co-op sources** (platform A + NISC), with per-source isolation and `status.csv`.
3. **Dashboard v1:** Now, time series and downloads.
4. **AEP incident crawl**, with the early-stop rule and the request cap.
5. **Maps:** county choropleth, then the incident map.
6. **Fill gaps:** AES Ohio, Ohio Edison and Columbus, as Justin finds their feeds in DevTools.
7. Optional: **KMZ export** of the county outage map for Justin's Max graphics system (he has existing GeoTIFF/KMZ tooling in other repos).

## Validation checks (write these as tests)
- AEP: county-report `cust_a` sum ≈ summary total (exact only when nothing is masked).
- AEP: incident crawl `n_out` sum == summary `total_outages` (when not capped).
- Every NISC `{name}` resolves, and `totalServed` is within ±20% of the last stored value (catches wrong-utility mixups).
- Decoded AEP points fall inside Ohio's bounding box.

## Data use
Collected from public utility outage maps for internal monitoring and newsroom use. Justin should review each utility's website terms before publishing derived maps or data on-air or online.
