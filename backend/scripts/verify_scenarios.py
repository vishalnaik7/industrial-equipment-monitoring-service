#!/usr/bin/env python3
"""End-to-end verification against a RUNNING deployment (local or Docker), using the supplied assets.

Run it against an EMPTY database - it ingests the seed data and every supplied sample, waits for the asynchronous
worker, and checks final processing results (not just acceptance). Exit code 0 = all checks passed.

usage: python scripts/verify_scenarios.py [--url http://localhost:8000] [--api-key KEY]
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.load_seed import batches  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "sample_payloads"
results: list[tuple[bool, str, str]] = []


class Api:
    def __init__(self, url, key):
        self.url, self.key = url.rstrip("/"), key

    def call(self, method, path, body=None, raw=None, ctype="application/json", as_bytes=False):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {**({"Content-Type": ctype} if data is not None else {}), **({"X-API-Key": self.key} if self.key else {})}
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                content = r.read()
                return r.status, (json.loads(content) if not as_bytes and content else content)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def get(self, path):
        return self.call("GET", path)[1]

    def wait_idle(self, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            if self.get("/ready")["queue_depth"] == 0:
                time.sleep(1.5)                       # let the worker finish recomputing dirty machines
                return
            time.sleep(0.5)
        raise SystemExit("worker did not drain the queue in time")


def check(ok, name, detail=""):
    results.append((bool(ok), name, detail))


def sample(name):
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def sections(doc):
    return {k: v for k, v in doc.items() if k in ("pulseforge", "thermexwatch", "maintaflow")} or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--api-key", default="")
    a = ap.parse_args()
    api = Api(a.url, a.api_key)

    # ---- service -----------------------------------------------------------------------------------------
    check(api.get("/health")["status"] == "ok", "health")
    meta = api.get("/v1/meta")
    check(meta["vendors"] == ["pulseforge", "thermexwatch", "maintaflow", "workorders"], "four sources registered",
          f"version {meta['version']}, rules {meta['rules_version']}")
    check(api.get("/v1/batches?limit=1")["count"] == 0, "database is empty before the run",
          "run this script against an empty database")

    # ---- seed: 8 batches, 25 records, final outcome ---------------------------------------------------------
    seed_ids = []
    for slug, body in batches():
        st, r = api.call("POST", f"/v1/ingest/{slug}", body)
        seed_ids.append(r["batch_id"])
        check(st == 202, f"seed accepted: {slug} {body.get('plant_id') or body.get('site_code') or body.get('factory_id')}",
              f"accepted={r['accepted_records']}")
    api.wait_idle()
    seed = [api.get(f"/v1/batches/{b}") for b in seed_ids]
    total = sum(s["accepted_records"] for s in seed)
    processed = sum(s["record_status_counts"].get("processed", 0) for s in seed)
    check(len(seed) == 8 and total == 25, "seed: 8 batches, 25 accepted records", f"{len(seed)} batches, {total} records")
    check(processed == 25 and all(s["state"] == "completed" for s in seed), "seed: all 25 records processed",
          "; ".join(f"{s['vendor']}:{s['state']}:{s['record_status_counts']}" for s in seed))

    # ---- invalid envelopes (synchronous 4xx, nothing stored) ----------------------------------------------
    for name, kw, code in [
        ("invalid JSON", dict(raw=b"{nope"), "INVALID_JSON"),
        ("wrong content type", dict(raw=b"{}", ctype="text/plain"), "UNSUPPORTED_MEDIA_TYPE"),
        ("empty record list", dict(body={"events": []}), "VALIDATION_ERROR"),
        ("non-object record", dict(body={"events": [{"event_id": "x"}, 5]}), "VALIDATION_ERROR"),
        ("vendor name mismatch", dict(body={"vendor": "ThermexWatch", "events": [{}]}), "VALIDATION_ERROR"),
        ("batch too large", dict(body={"events": [{}] * 5001}), "BATCH_TOO_LARGE"),
    ]:
        st, r = api.call("POST", "/v1/ingest/pulseforge", **kw)
        detail = r["error"].get("details", [{}])[0].get("field", "") if r and r.get("error") else ""
        check(st in (400, 413, 415, 422) and r["error"]["code"] == code, f"invalid envelope rejected: {name}",
              f"HTTP {st} {r['error']['code']} {detail}".strip())
    check(api.call("POST", "/v1/ingest/acme", {})[0] == 404, "unknown source rejected", "HTTP 404")
    check(api.get("/v1/batches?limit=50")["count"] == 8, "invalid requests stored nothing")

    # ---- every supplied sample, per vendor section ---------------------------------------------------------
    outcome = {}
    order = ["pulseforge_sample", "thermexwatch_sample", "maintaflow_sample", "happy_path", "duplicates", "malformed",
             "retry_cases", "out_of_order", "conflicting_updates"]
    for name in order:
        doc = sample(name)
        parts = sections(doc) or {next(s for s in ("pulseforge", "thermexwatch", "maintaflow") if s in name): doc}
        for slug, body in parts.items():
            st, r = api.call("POST", f"/v1/ingest/{slug}", body)
            outcome[(name, slug)] = (st, r)
    api.wait_idle()
    for (name, slug), (st, r) in outcome.items():
        b = api.get(f"/v1/batches/{r['batch_id']}")
        outcome[(name, slug)] = b
        check(st == 202 and b["state"] != "processing", f"sample {name} / {slug}: processed",
              f"{b['state']} counts={b['record_status_counts']} dup={b['duplicate_records']} rev={b['revised_records']}"
              + (" errors=" + ",".join(f"{e['vendor_record_id']}:{e['error_code']}" for e in b["errors"]) if b["errors"] else ""))

    # duplicates
    st, r = api.call("POST", "/v1/ingest/pulseforge", sample("duplicates")["pulseforge"])
    check(st == 200 and r["replayed"], "duplicates: identical request replay returns the original batch", f"HTTP {st}")
    tw_r = api.get("/v1/records/thermexwatch/TW-8801-R")["raw_revisions"][0]
    check(tw_r["error_code"] == "SEMANTIC_DUPLICATE", "duplicates: same content under a new id detected",
          f"TW-8801-R -> {tw_r['status']} {tw_r['error_code']}")
    # malformed
    pf = outcome[("malformed", "pulseforge")]
    tw = outcome[("malformed", "thermexwatch")]
    check(pf["record_status_counts"] == {"rejected": 2}, "malformed / PulseForge section: both records rejected",
          ", ".join(f"{e['vendor_record_id']}:{e['error_code']}" for e in pf["errors"]))
    tw912 = api.get("/v1/records/thermexwatch/TW-9912")["normalized_event"]
    check(tw["record_status_counts"] == {"processed": 1, "rejected": 1} and tw912 is not None,
          "malformed / ThermexWatch section: 1 processed with warnings, 1 rejected",
          f"TW-9911:{tw['errors'][0]['error_code']}; TW-9912 warnings={tw912['warnings']}")
    # partial failure + corrected resend
    rc = sample("retry_cases")["pulseforge"]
    fixed = json.loads(json.dumps(rc))
    fixed["events"][1]["event_time"] = "2026-04-18T08:47:30Z"
    fixed["batch_generated_at"] = "2026-04-18T08:55:00Z"
    api.call("POST", "/v1/ingest/pulseforge", fixed)
    api.wait_idle()
    revs = api.get("/v1/records/pulseforge/PF-1502")["raw_revisions"]
    check([(x["status"], x["authoritative"]) for x in revs] == [("rejected", False), ("processed", True)],
          "partial failure: corrected resend of a rejected record is processed", f"PF-1502 revisions={len(revs)}")
    # out of order (separate events)
    t1, t2 = api.get("/v1/records/pulseforge/PF-1301"), api.get("/v1/records/pulseforge/PF-1302")
    check(t1["normalized_event"] and t2["normalized_event"], "out-of-order: both separate events kept",
          f"PF-1301 {t1['normalized_event']['event_time']} / PF-1302 {t2['normalized_event']['event_time']}")
    # conflicting updates
    m8 = api.get("/v1/machines/EQ-008")
    check(m8["derived_status"] == "critical" and "CONFLICTING_SOURCES" in m8["reason_codes"],
          "conflicting updates: pessimistic and flagged", f"EQ-008 {m8['derived_status']} {m8['reason_codes']}")
    # work orders
    wo = api.get("/v1/records/workorders/WO-1002")["normalized_event"]
    check(wo["maintenance_status"] == "closed" and wo["inspection_result"] == "passed", "work orders: WO-1002 normalized",
          f"{wo['event_code']} {wo['event_time']}")
    m4 = api.get("/v1/machines/EQ-004")
    check(any(r["code"] == "WORK_ORDER_VERIFIED" or r["code"] == "SUPERSEDED_BY_COMPLETED_MAINTENANCE"
              for r in m4["reasons"]), "work orders: EQ-004 alerts resolved", f"EQ-004 {m4['derived_status']} L{m4['attention_level']}")
    # revisions: a delayed older vendor version must not win
    old = json.loads(json.dumps(sample("pulseforge_sample")))
    old["batch_generated_at"] = "2026-04-18T07:00:00Z"
    old["events"] = [dict(old["events"][0], severity="low")]
    api.call("POST", "/v1/ingest/pulseforge", old)
    api.wait_idle()
    last = api.get("/v1/records/pulseforge/PF-1001")["raw_revisions"][-1]
    check(last["error_code"] == "STALE_REVISION" and not last["authoritative"],
          "revisions: delayed older vendor version not applied", last["error_message"] or "")
    # traceability: byte-exact body + per-record payload
    # a genuinely new record (new id AND new content - same content under a new id would be a semantic duplicate),
    # sent with the vendor's own formatting
    raw_body = (json.dumps(sample("maintaflow_sample"), indent=4).replace('"MF-501"', '"MF-TRACE-1"')
                .replace("Belt wear visible", "Belt wear re-checked").encode())
    st, r = api.call("POST", "/v1/ingest/maintaflow", raw=raw_body)
    api.wait_idle()
    st2, got = api.call("GET", f"/v1/batches/{r['batch_id']}/raw", as_bytes=True)
    check(st2 == 200 and got == raw_body, "traceability: request body stored byte-for-byte", f"{len(raw_body)} bytes")
    rec = api.get("/v1/records/maintaflow/MF-TRACE-1")
    check(rec["raw_revisions"][0]["payload"] == json.loads(raw_body)["records"][0] and rec["normalized_event"],
          "traceability: record payload unchanged and linked to its canonical event")
    # distinct observation under a new id (only signal quality differs) is NOT a semantic duplicate
    tw1 = dict(sample("thermexwatch_sample")["readings"][1], readingId="TW-DQ-1", timestampMs=1776499999000)
    tw2 = dict(tw1, readingId="TW-DQ-2", signal_quality="POOR")
    for r in (tw1, tw2):
        api.call("POST", "/v1/ingest/thermexwatch", {"source": "ThermexWatch", "site_code": "PLANT_01", "readings": [r]})
    api.wait_idle()
    s2 = api.get("/v1/records/thermexwatch/TW-DQ-2")["raw_revisions"][0]
    check(s2["status"] == "processed", "duplicates: a distinct observation under a new id is kept", f"TW-DQ-2 -> {s2['status']}")
    # future-timestamp policy
    future = dict(tw1, readingId="TW-FUTURE", timestampMs=int((time.time() + 86400 * 30) * 1000))
    api.call("POST", "/v1/ingest/thermexwatch", {"source": "ThermexWatch", "site_code": "PLANT_01", "readings": [future]})
    api.wait_idle()
    fr = api.get("/v1/records/thermexwatch/TW-FUTURE")["raw_revisions"][0]
    check(fr["error_code"] == "FUTURE_TIMESTAMP", "timestamps: a reading 30 days in the future is rejected",
          f"TW-FUTURE -> {fr['status']} {fr['error_code']}")
    # work-order scope: WO-1002 (power_check) resolved EQ-004's power alerts only
    m4r = {r["code"]: r for r in m4["reasons"]}
    check("POWER_ANOMALY" in m4r and m4r["POWER_ANOMALY"]["level"] == 0 and "WO-1002" in m4r["POWER_ANOMALY"]["detail"],
          "work orders: power check resolves the power alerts it covers", m4r.get("WORK_ORDER_VERIFIED", {}).get("detail", "")[-60:])

    # summary + dead letters
    s = api.get("/v1/summary")
    check(s["machines_total"] == 8 and sum(s["by_status"].values()) == 8, "summary covers all machines",
          f"by_status={ {k: v for k, v in s['by_status'].items() if v} } critical={[m['machine_id'] for m in s['critical_machines']]}")
    dl = api.get("/v1/dead-letters")
    check(dl["count"] >= 1, "dead letters listed", ", ".join(f"{d['vendor_record_id']}:{d['error_code']}" for d in dl["items"]))

    width = max(len(n) for _, n, _ in results)
    for ok, name, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    failed = sum(not ok for ok, _, _ in results)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
