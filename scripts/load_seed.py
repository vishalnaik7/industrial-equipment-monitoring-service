#!/usr/bin/env python3
"""Load data/seed/*.csv into a running API by wrapping rows in each vendor's envelope.

usage: python scripts/load_seed.py [--url http://localhost:8000] [--api-key KEY]
"""
import argparse
import csv
import itertools
import json
import urllib.request
from pathlib import Path

SEED = Path(__file__).resolve().parent.parent / "data" / "seed"


def rows(name):
    with (SEED / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def post(url, slug, body, key):
    req = urllib.request.Request(f"{url}/v1/ingest/{slug}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **({"X-API-Key": key} if key else {})})
    with urllib.request.urlopen(req) as r:
        out = json.load(r)
    print(f"{slug:13s} {body.get('plant_id') or body.get('site_code') or body.get('factory_id')}: "
          f"accepted={out['accepted_records']} duplicates={out['duplicate_records']} batch={out['batch_id']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--api-key", default="")
    a = ap.parse_args()
    key = lambda r, k: r[k]
    for plant, grp in itertools.groupby(sorted(rows("pulseforge_events.csv"), key=lambda r: r["plant_id"]),
                                        key=lambda r: r["plant_id"]):
        events = [{k: v for k, v in r.items() if k not in ("vendor", "plant_id", "ingestion_time")} for r in grp]
        post(a.url, "pulseforge", {"vendor": "PulseForge", "plant_id": plant, "events": events}, a.api_key)
    for site, grp in itertools.groupby(sorted(rows("thermexwatch_readings.csv"), key=lambda r: r["site_code"]),
                                       key=lambda r: r["site_code"]):
        rd = [{k: v for k, v in r.items() if k not in ("source", "site_code", "received_at_ms")} for r in grp]
        for r in rd:
            r.update(readingId=r.pop("reading_id"), assetCode=r.pop("asset_code"),
                     productionLine=r.pop("production_line"), timestampMs=r.pop("timestamp_ms"),
                     alertCode=r.pop("alert_code"), signal_quality=r["signal_quality"])
        post(a.url, "thermexwatch", {"source": "ThermexWatch", "site_code": site, "readings": rd}, a.api_key)
    for f, grp in itertools.groupby(sorted(rows("maintaflow_records.csv"), key=lambda r: r["factory_id"]),
                                    key=lambda r: r["factory_id"]):
        rc = [{k: (v if v != "" else None) for k, v in r.items() if k not in ("provider_name", "factory_id")}
              for r in grp]
        post(a.url, "maintaflow", {"provider_name": "MaintaFlow", "factory_id": f, "records": rc}, a.api_key)


if __name__ == "__main__":
    main()
