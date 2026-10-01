"""Deterministic, explainable derivation of the machine operational attention view.

Pure function: (asset, normalized events, settings) -> state dict. No I/O, easy to unit test.

Rules (RULES_VERSION 1.0)
-------------------------
1. Per sensor vendor only the newest reading (by EVENT time, never arrival time) counts, so out-of-order
   delivery is harmless and a newer RECOVERY/NOMINAL from a vendor clears that vendor's earlier alert.
2. A vendor's newest reading older than `relevance_window` before the machine's newest event is ignored.
3. Each active reading yields reasons from (a) the vendor's own alert and (b) our threshold checks against the
   asset's rated limits. Poor signal quality downgrades a reason by one level.
4. Cross-vendor conflicts resolve pessimistically (the alert stands) and are flagged CONFLICTING_SOURCES.
5. Maintenance records: inspection defects, overdue/due-soon service, pending calibration, operator-note
   keywords. A later `completed` maintenance record supersedes earlier inspections/notes.
6. attention_level = max reason level (0..4); +1 when two different sources independently reach >= high.
7. status by level: healthy, watch, degraded, at_risk, critical. Asset under maintenance -> under_maintenance.
"""
from __future__ import annotations

from datetime import timedelta

from . import timeutil
from .config import Settings
from .normalize import ALERT_CODES, CLEAR_CODES, SEVERITY_LABELS

RULES_VERSION = "1.0"
STATUS_BY_LEVEL = ["healthy", "watch", "degraded", "at_risk", "critical"]
NOTE_KEYWORDS = {2: ("stall", "failure", "failed", "leak", "smoke", "burning", "overheat"),
                 1: ("noise", "vibration", "unusual", "wear")}


def _reason(code, level, source, ref, time, detail):
    return {"code": code, "level": level, "level_label": SEVERITY_LABELS[level], "source": source,
            "record_id": ref, "event_time": time, "detail": detail}


def _signal(e: dict) -> dict:
    keys = ("event_code", "severity", "temperature_c", "vibration_mm_s", "vibration_g", "power_kw",
            "machine_state", "signal_quality", "inspection_result", "maintenance_status", "days_since_service",
            "note")
    return {"source": e["vendor"], "record_id": e["source_record_id"], "kind": e["kind"],
            "event_time": e["event_time"], **{k: e[k] for k in keys if e.get(k) is not None}}


def derive_machine_state(asset: dict, events: list[dict], s: Settings) -> dict:
    events = sorted(events, key=lambda e: (e["event_time"], e["source_record_id"]))
    if not events:
        status = "under_maintenance" if asset["asset_status"] == "maintenance" else "unknown"
        return _result(asset, status, 0, [_reason("NO_DATA", 0, "system", None, None, "no processed events")],
                       [], None, None, s)

    latest_all = events[-1]["event_time"]
    cutoff = timeutil.to_iso(timeutil.from_iso(latest_all) - timedelta(minutes=s.relevance_window_minutes))
    sensors = [e for e in events if e["kind"] == "sensor"]
    maint = [e for e in events if e["kind"] != "sensor"]
    reasons: list[dict] = []

    # ---- sensors ---------------------------------------------------------------------------------------
    latest_by_vendor: dict[str, dict] = {}
    for e in sensors:                                   # ascending => last write wins = newest
        latest_by_vendor[e["vendor"]] = e
    active = []
    for v, e in latest_by_vendor.items():
        if e["event_time"] < cutoff:
            reasons.append(_reason("STALE_SIGNAL_IGNORED", 0, v, e["source_record_id"], e["event_time"],
                                   f"{v} latest reading is older than the {s.relevance_window_minutes} min "
                                   "relevance window"))
        else:
            active.append(e)

    for e in active:
        src, ref, t = e["vendor"], e["source_record_id"], e["event_time"]
        poor = e.get("signal_quality") == "poor"

        def add(code, lvl, detail, e=e, src=src, ref=ref, t=t, poor=poor):
            if poor and e["event_code"] != "SENSOR_DEGRADED" and lvl > 0:
                lvl -= 1
                detail += " (downgraded one level: poor signal quality)"
            reasons.append(_reason(code, lvl, src, ref, t, detail))

        if e["event_code"] in ALERT_CODES:
            add(e["event_code"], e["severity"] or 0,
                f"{src} reported {e['event_code']} with severity {SEVERITY_LABELS[e['severity'] or 0]}")
        temp, vib, kw = e.get("temperature_c"), e.get("vibration_mm_s"), e.get("power_kw")
        if temp is not None and asset["rated_max_temp_c"]:
            lim = asset["rated_max_temp_c"]
            r = temp / lim
            if r >= 1.10:
                add("TEMP_OVER_RATED_LIMIT", 4, f"{temp:.1f}C is {r:.0%} of rated max {lim}C")
            elif r > 1.0:
                add("TEMP_OVER_RATED_LIMIT", 3, f"{temp:.1f}C exceeds rated max {lim}C")
            elif r >= 0.95:
                add("TEMP_NEAR_RATED_LIMIT", 2, f"{temp:.1f}C is {r:.0%} of rated max {lim}C")
        if vib is not None and asset["rated_max_vibration_mm_s"]:
            lim = asset["rated_max_vibration_mm_s"]
            r = vib / lim
            if r >= 1.25:
                add("VIBRATION_OVER_RATED_LIMIT", 4, f"{vib} mm/s is {r:.0%} of rated max {lim}")
            elif r > 1.0:
                add("VIBRATION_OVER_RATED_LIMIT", 3, f"{vib} mm/s exceeds rated max {lim}")
            elif r >= 0.9:
                add("VIBRATION_NEAR_RATED_LIMIT", 2, f"{vib} mm/s is {r:.0%} of rated max {lim}")
        if kw is not None and asset["baseline_power_kw"] and e.get("machine_state") == "running":
            base = asset["baseline_power_kw"]
            dev = abs(kw - base) / base
            if dev >= 0.40:
                add("POWER_DEVIATION", 3, f"{kw} kW deviates {dev:.0%} from baseline {base} kW")
            elif dev >= 0.25:
                add("POWER_DEVIATION", 2, f"{kw} kW deviates {dev:.0%} from baseline {base} kW")

    clears = [e for e in active if e["event_code"] in CLEAR_CODES]
    for c in clears:                                    # informational (level 0) so the explanation is complete
        reasons.append(_reason(f"{c['event_code']}_REPORTED", 0, c["vendor"], c["source_record_id"],
                               c["event_time"], f"{c['vendor']} latest reading reports {c['event_code']}"))
    alerting = [r for r in reasons if r["level"] >= 2]
    if alerting and clears:
        reasons.append(_reason("CONFLICTING_SOURCES", 1, "system", None, None,
                               "; ".join(f"{c['vendor']} reports {c['event_code']} at {c['event_time']}" for c in clears)
                               + " but another source still reports an alert; alert kept (pessimistic)"))

    # ---- maintenance -------------------------------------------------------------------------------------
    completed = [e for e in maint if e["maintenance_status"] == "completed"]
    last_done = completed[-1]["event_time"] if completed else ""

    def latest(pred):
        c = [e for e in maint if pred(e)]
        return c[-1] if c else None

    def m_add(e, code, lvl, detail):
        if e.get("confidence") is not None and e["confidence"] < 0.5 and lvl > 0:
            lvl -= 1
            detail += " (downgraded one level: low manual confidence)"
        reasons.append(_reason(code, lvl, e["vendor"], e["source_record_id"], e["event_time"], detail))

    def superseded(e, what):
        reasons.append(_reason("SUPERSEDED_BY_COMPLETED_MAINTENANCE", 0, e["vendor"], e["source_record_id"],
                               e["event_time"], f"{what} predates completed maintenance"))

    insp = latest(lambda e: e["kind"] == "inspection")
    if insp and insp["event_time"] > last_done:
        res = insp.get("inspection_result")
        if res == "major_defect_found":
            m_add(insp, "INSPECTION_MAJOR_DEFECT", 3, f"major defect: {insp.get('note')}")
        elif res == "minor_defect_found":
            m_add(insp, "INSPECTION_MINOR_DEFECT", 1, f"minor defect: {insp.get('note')}")
        elif res == "passed_with_observation":
            m_add(insp, "INSPECTION_OBSERVATION", 1, f"observation: {insp.get('note')}")
    elif insp:
        superseded(insp, "inspection")
    ms = latest(lambda e: e.get("maintenance_status") not in (None, "unknown"))
    if ms and ms["maintenance_status"] == "overdue":
        m_add(ms, "MAINTENANCE_OVERDUE", 2, f"service overdue ({ms.get('days_since_service')} days since last)")
    elif ms and ms["maintenance_status"] == "due_soon":
        m_add(ms, "MAINTENANCE_DUE_SOON", 1, f"service due soon ({ms.get('days_since_service')} days since last)")
    cal = latest(lambda e: e["kind"] == "calibration")
    if cal and cal.get("inspection_result") == "calibration_pending" and cal["event_time"] > last_done:
        m_add(cal, "CALIBRATION_PENDING", 1, f"calibration pending: {cal.get('note')}")
    note = latest(lambda e: e["kind"] == "operator_note")
    if note and note["event_time"] > last_done and note.get("note"):
        txt = note["note"].lower()
        lvl = max((l for l, ks in NOTE_KEYWORDS.items() if any(k in txt for k in ks)), default=0)
        if lvl:
            m_add(note, "OPERATOR_REPORTED_ISSUE", lvl, f"operator note: {note['note']}")
    elif note:
        superseded(note, "operator note")

    # ---- aggregate ---------------------------------------------------------------------------------------
    level = max((r["level"] for r in reasons), default=0)
    high_sources = {r["source"] for r in reasons if r["level"] >= 3 and r["source"] != "system"}
    if len(high_sources) >= 2 and level < 4:
        level += 1
        reasons.append(_reason("MULTI_SOURCE_CORROBORATION", level, "system", None, None,
                               f"independent sources {sorted(high_sources)} both report high severity"))
    status = STATUS_BY_LEVEL[level]
    if asset["asset_status"] == "maintenance":
        status = "under_maintenance"
        reasons.append(_reason("ASSET_UNDER_MAINTENANCE", 0, "reference", None, None, "asset_status=maintenance"))
    elif asset["asset_status"] not in ("active", None):
        status = "offline"

    signals = [_signal(e) for e in active]
    for pred in (lambda e: e["kind"] == "inspection", lambda e: e.get("maintenance_status") not in (None, "unknown"),
                 lambda e: e["kind"] == "calibration", lambda e: e["kind"] == "operator_note"):
        e = latest(pred)
        if e and _signal(e) not in signals:
            signals.append(_signal(e))
    signals.sort(key=lambda x: x["event_time"], reverse=True)
    state = next((e["machine_state"] for e in reversed(sensors) if e.get("machine_state")), None)
    return _result(asset, status, level, reasons, signals, latest_all, state, s)


def _result(asset, status, level, reasons, signals, latest_time, machine_state, s: Settings) -> dict:
    reasons = sorted(reasons, key=lambda r: -r["level"])
    refs = {(r["source"], r["record_id"]) for r in reasons if r["record_id"]} | \
           {(x["source"], x["record_id"]) for x in signals}
    return {
        "machine_id": asset["machine_id"], "plant_id": asset["plant_id"], "line_id": asset["line_id"],
        "derived_status": status, "attention_level": level,
        "needs_attention": level >= s.needs_attention_level,
        "reason_codes": list(dict.fromkeys(r["code"] for r in reasons if r["level"] >= 1)),
        "reasons": reasons, "latest_signals": signals, "machine_state": machine_state,
        "latest_relevant_event_time": latest_time,
        "source_event_refs": [{"source": a, "record_id": b} for a, b in sorted(refs)],
        "rules_version": RULES_VERSION,
    }
