"""Read models: machine attention view and plant/line summaries. Processing status is computed at read time."""
from __future__ import annotations

import json

from . import timeutil
from .config import Settings

STATUS_ORDER = ["critical", "at_risk", "degraded", "watch", "healthy", "under_maintenance", "offline", "unknown"]


def _ingest_counts(conn) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT machine_hint m, status, COUNT(*) n FROM raw_records WHERE is_latest=1 AND "
                          "machine_hint IS NOT NULL GROUP BY m, status"):
        out.setdefault(r["m"], {})[r["status"]] = r["n"]
    return out


def _processing(row, counts: dict, now, s: Settings) -> dict:
    pending = counts.get("pending", 0) + counts.get("processing", 0)
    dead, rejected = counts.get("dead", 0), counts.get("rejected", 0)
    age = None
    if row is not None and row["latest_relevant_event_time"]:
        age = int((now - timeutil.from_iso(row["latest_relevant_event_time"])).total_seconds())
    if pending:
        status = "pending"
    elif dead:
        status = "failed"
    elif rejected:
        status = "partial"        # some of this machine's records could not be processed
    elif row is None:
        status = "no_data"
    elif age is not None and age > s.stale_after_minutes * 60:
        status = "stale"
    else:
        status = "fresh"
    return {"status": status, "pending_records": pending, "failed_records": dead, "rejected_records": rejected,
            "data_age_seconds": age, "stale_after_minutes": s.stale_after_minutes}


def _view(asset, row, counts, now, s) -> dict:
    base = {"machine_id": asset["machine_id"], "plant_id": asset["plant_id"], "line_id": asset["line_id"],
            "machine_type": asset["machine_type"], "criticality": asset["criticality"]}
    proc = _processing(row, counts, now, s)
    if row is None:
        return {**base, "derived_status": "unknown", "needs_attention": False, "attention_level": 0,
                "attention_label": "none", "reason_codes": [], "reasons": [], "latest_signals": [],
                "machine_state": None, "latest_relevant_event_time": None, "source_event_refs": [],
                "last_processed_at": None, "state_version": 0, "rules_version": None, "processing_status": proc}
    labels = ["none", "low", "medium", "high", "critical"]
    return {**base, "derived_status": row["derived_status"], "needs_attention": bool(row["needs_attention"]),
            "attention_level": row["attention_level"], "attention_label": labels[row["attention_level"]],
            "reason_codes": json.loads(row["reason_codes"]), "reasons": json.loads(row["reasons"]),
            "latest_signals": json.loads(row["latest_signals"]), "machine_state": row["machine_state"],
            "latest_relevant_event_time": row["latest_relevant_event_time"],
            "source_event_refs": json.loads(row["source_event_refs"]), "last_processed_at": row["last_processed_at"],
            "state_version": row["state_version"], "rules_version": row["rules_version"], "processing_status": proc}


def list_machines(conn, s: Settings, now, plant_id=None, line_id=None, status=None, needs_attention=None) -> list[dict]:
    states = {r["machine_id"]: r for r in conn.execute("SELECT * FROM machine_state")}
    counts = _ingest_counts(conn)
    out = []
    for a in conn.execute("SELECT * FROM assets ORDER BY machine_id"):
        if plant_id and a["plant_id"] != plant_id or line_id and a["line_id"] != line_id:
            continue
        v = _view(a, states.get(a["machine_id"]), counts.get(a["machine_id"], {}), now, s)
        if status and v["derived_status"] != status:
            continue
        if needs_attention is not None and v["needs_attention"] != needs_attention:
            continue
        out.append(v)
    out.sort(key=lambda v: (-v["attention_level"], v["machine_id"]))
    return out


def get_machine(conn, s: Settings, now, machine_id: str) -> dict | None:
    a = conn.execute("SELECT * FROM assets WHERE machine_id=?", (machine_id,)).fetchone()
    if not a:
        return None
    row = conn.execute("SELECT * FROM machine_state WHERE machine_id=?", (machine_id,)).fetchone()
    counts = _ingest_counts(conn).get(machine_id, {})
    return _view(a, row, counts, now, s)


def summarize(conn, s: Settings, now, plant_id: str | None = None) -> dict:
    machines = list_machines(conn, s, now, plant_id=plant_id)
    by_status = {k: 0 for k in STATUS_ORDER}
    lines: dict[tuple, dict] = {}
    for ln in conn.execute("SELECT * FROM lines"):
        if plant_id and ln["plant_id"] != plant_id:
            continue
        lines[(ln["plant_id"], ln["line_id"])] = {
            "plant_id": ln["plant_id"], "line_id": ln["line_id"], "line_name": ln["line_name"],
            "machines_total": 0, "machines_needing_attention": 0, "critical_machines": 0, "by_status": {}}
    for m in machines:
        by_status[m["derived_status"]] += 1
        L = lines.get((m["plant_id"], m["line_id"]))
        if L is None:
            continue
        L["machines_total"] += 1
        L["machines_needing_attention"] += int(m["needs_attention"])
        L["critical_machines"] += int(m["derived_status"] == "critical")
        L["by_status"][m["derived_status"]] = L["by_status"].get(m["derived_status"], 0) + 1
    ranked = sorted(lines.values(), key=lambda L: (-L["machines_needing_attention"], -L["critical_machines"],
                                                    L["plant_id"], L["line_id"]))
    brief = lambda m: {"machine_id": m["machine_id"], "plant_id": m["plant_id"], "line_id": m["line_id"],
                       "attention_level": m["attention_level"], "reason_codes": m["reason_codes"],
                       "latest_relevant_event_time": m["latest_relevant_event_time"]}
    plants = sorted({m["plant_id"] for m in machines})
    return {
        "scope": {"plant_id": plant_id} if plant_id else {"plants": plants},
        "generated_at": timeutil.to_iso(now), "machines_total": len(machines),
        "machines_needing_attention": sum(m["needs_attention"] for m in machines),
        "by_status": by_status,
        "lines_ranked_by_attention": ranked,
        "critical_machines": [brief(m) for m in machines if m["derived_status"] == "critical"],
        "data_quality": {
            "stale_machines": [m["machine_id"] for m in machines if m["processing_status"]["status"] == "stale"],
            "failed_machines": [m["machine_id"] for m in machines if m["processing_status"]["status"] == "failed"],
            "partial_machines": [m["machine_id"] for m in machines if m["processing_status"]["status"] == "partial"],
            "pending_machines": [m["machine_id"] for m in machines if m["processing_status"]["status"] == "pending"],
            "no_data_machines": [m["machine_id"] for m in machines if m["processing_status"]["status"] == "no_data"],
        },
    }
