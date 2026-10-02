#!/usr/bin/env python3
"""Load data/seed/*.csv into a running API by wrapping rows in each source's envelope (3 vendors + CMMS work orders).

The CSVs are flat exports of the vendor API shapes: per-batch columns (vendor/source name, plant/site/factory code)
move into the envelope, and ThermexWatch's snake_case export columns are mapped back to its camelCase API field names.
Cell values are sent unchanged (an empty CSV cell becomes a missing value).

usage: python scripts/load_seed.py [--url http://localhost:8000] [--api-key KEY]
"""
import argparse
import csv
import itertools
import json
import urllib.request
from pathlib import Path

SEED = Path(__file__).resolve().parent.parent / "data" / "seed"
TW_FIELDS = {"reading_id": "readingId", "asset_code": "assetCode", "production_line": "productionLine",
             "timestamp_ms": "timestampMs", "alert_code": "alertCode"}


def rows(name):
    with (SEED / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def grouped(name, key):
    return itertools.groupby(sorted(rows(name), key=lambda r: r[key]), key=lambda r: r[key])


def batches():
    """Yield (source slug, request body) for every seed batch, one batch per source and plant."""
    for plant, grp in grouped("pulseforge_events.csv", "plant_id"):
        events = [{k: v for k, v in r.items() if k not in ("vendor", "plant_id", "ingestion_time")} for r in grp]
        yield "pulseforge", {"vendor": "PulseForge", "plant_id": plant, "events": events}
    for site, grp in grouped("thermexwatch_readings.csv", "site_code"):
        readings = [{TW_FIELDS.get(k, k): v for k, v in r.items() if k not in ("source", "site_code", "received_at_ms")}
                    for r in grp]
        yield "thermexwatch", {"source": "ThermexWatch", "site_code": site, "readings": readings}
    for factory, grp in grouped("maintaflow_records.csv", "factory_id"):
        records = [{k: (v if v != "" else None) for k, v in r.items() if k not in ("provider_name", "factory_id")}
                   for r in grp]
        yield "maintaflow", {"provider_name": "MaintaFlow", "factory_id": factory, "records": records}
    for plant, grp in grouped("maintenance_work_orders.csv", "plant_id"):
        work_orders = [{k: (v if v != "" else None) for k, v in r.items()} for r in grp]
        yield "workorders", {"source": "CMMS", "plant_id": plant, "work_orders": work_orders}


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
    for slug, body in batches():
        post(a.url, slug, body, a.api_key)


if __name__ == "__main__":
    main()
