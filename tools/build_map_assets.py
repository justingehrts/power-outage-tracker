"""One-off script: builds the two small map files the dashboard ships with.

    python tools/build_map_assets.py

Writes (both committed to the repo, so the dashboard never downloads from Census):
  app/assets/ohio_counties.geojson   Ohio county outlines (US Census cartographic boundaries,
                                     via the plotly datasets mirror), ids are 5-digit FIPS codes
  app/assets/zip_to_county.csv       Ohio ZIP (ZCTA) -> county with the largest land overlap
                                     (US Census 2020 ZCTA-to-county relationship file)

ZIP codes can straddle counties, so zip_to_county is an approximation. It is only used for
co-ops that report by ZIP code and not by county (Union Rural Electric, Logan County Electric).
"""
import io
import json
from pathlib import Path

import pandas as pd
import requests

COUNTIES_URL = "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"
ZCTA_URL = ("https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
            "tab20_zcta520_county20_natl.txt")
OUT = Path(__file__).resolve().parent.parent / "app" / "assets"
OHIO = "39"


def round_coords(c):
    return [round_coords(x) for x in c] if isinstance(c[0], list) else [round(c[0], 3), round(c[1], 3)]


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    gj = requests.get(COUNTIES_URL, timeout=120).json()
    feats = []
    for f in gj["features"]:
        p = f["properties"]
        if p["STATE"] != OHIO:
            continue
        geom = dict(f["geometry"], coordinates=round_coords(f["geometry"]["coordinates"]))
        feats.append({"type": "Feature", "id": f["id"], "properties": {"name": p["NAME"]},
                      "geometry": geom})
    assert len(feats) == 88, f"expected 88 Ohio counties, got {len(feats)}"
    (OUT / "ohio_counties.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))

    z = pd.read_csv(io.StringIO(requests.get(ZCTA_URL, timeout=180).text), sep="|", dtype=str)
    z["AREALAND_PART"] = z["AREALAND_PART"].astype(float)
    best = z.sort_values("AREALAND_PART", ascending=False).groupby("GEOID_ZCTA5_20").head(1)
    oh = best[best["GEOID_COUNTY_20"].str.startswith(OHIO, na=False)]
    names = {f["id"]: f["properties"]["name"] for f in feats}
    out = pd.DataFrame({"zip": oh["GEOID_ZCTA5_20"], "county_fips": oh["GEOID_COUNTY_20"]})
    out["county_name"] = out["county_fips"].map(names)
    out.sort_values("zip").to_csv(OUT / "zip_to_county.csv", index=False)
    print(f"{len(feats)} counties, {len(out)} Ohio ZIPs written to {OUT}")


if __name__ == "__main__":
    main()
