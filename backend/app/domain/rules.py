"""Deterministic, explainable derivation of the machine operational attention view.

Pure function: (asset, normalized events, settings) -> state dict. No I/O, easy to unit test.

Rules (RULES_VERSION 1.3)
-------------------------
Two different freshness policies, on purpose:
  * SENSOR READINGS are point-in-time measurements. They expire (rules 1-2).
  * MAINTENANCE FINDINGS (inspection defects/observations, pending calibration, operator-reported issues, service
    status) describe a condition that stays true until someone deals with it. They never expire by age; they are
    resolved only by a newer record of the same kind, by completed maintenance, or by a verified work order (rule 5, 8).

1. Per sensor vendor only the newest reading (by EVENT time, never arrival time) counts, so out-of-order
   delivery is harmless and a newer RECOVERY/NOMINAL from a vendor clears that vendor's earlier alert. Two readings
   from one vendor with the SAME event time: the more severe one counts (pessimistic), then the record id (1.3).
2. A vendor's newest reading older than `relevance_window` before the machine's newest SENSOR reading (any vendor) is
   ignored as STALE_SIGNAL_IGNORED. The anchor is sensor time only (1.2): a later inspection or work order must not make
   current sensor readings look stale.
3. Each active reading yields reasons from (a) the vendor's own alert and (b) our threshold checks against the
   asset's rated limits. Poor signal quality downgrades a reason by one level.
4. Cross-vendor conflicts resolve pessimistically (the alert stands) and are flagged CONFLICTING_SOURCES.
5. Maintenance findings: the NEWEST record of each kind decides (inspection, calibration, operator note, service
   status). A newer passed inspection resolves an earlier defect (FINDING_RESOLVED_BY_INSPECTION); a newer calibration
   that is no longer pending resolves pending calibration; a later `completed` maintenance record resolves every
   earlier finding. An unresolved finding stays in the output however old it is, and its age is stated.
8. CMMS work orders: a work order CLOSED with a PASSED post-maintenance check resolves only the findings in its
   SCOPE (1.3) - the reason categories implied by its maintenance_type plus its trigger (vocabulary.WO_TYPE_SCOPE,
   WO_TRIGGER_SCOPE): a power check clears power alerts, not vibration, temperature or inspection findings. Only
   findings dated at or before its close are resolved, so an older repair never clears a newer fault. Resolved reasons
   stay in the explanation at level 0 with the work order named; an unknown maintenance type resolves nothing. An OPEN work order is shown as
   acknowledged but does not lower attention (the fault is not fixed yet). A closed work order whose check FAILED
   raises POST_MAINTENANCE_CHECK_FAILED; one whose check is still pending raises POST_MAINTENANCE_CHECK_PENDING.
6. attention_level = max reason level (0..4); +1 when two different sources independently reach >= high.
7. status by level: healthy, watch, degraded, at_risk, critical. Asset under maintenance -> under_maintenance.
"""
from __future__ import annotations

import json
from datetime import timedelta

from app.core import timeutil
from app.core.config import Settings
from app.domain.normalization.vocabulary import (ALERT_CODES, CLEAR_CODES, REASON_CATEGORY, SEVERITY_LABELS,
                                                 WO_OPEN_STATES)

RULES_VERSION = "1.3"
STATUS_BY_LEVEL = ["healthy", "watch", "degraded", "at_risk", "critical"]
DEFECT_RESULTS = ("major_defect_found", "minor_defect_found")
NOTE_KEYWORDS = {2: ("stall", "failure", "failed", "leak", "smoke", "burning", "overheat"),
                 1: ("noise", "vibration", "unusual", "wear")}


def _reason(code, level, source, ref, time, detail):
    return {"code": code, "level": level, "level_label": SEVERITY_LABELS[level], "source": source,
            "record_id": ref, "event_time": time, "detail": detail}


def _ratio(value: float, limit: float) -> float:
    """value / limit rounded to 6 decimals, so an exact boundary (8.1 of 9.0 = 90%) is not lost to float error."""
    return round(value / limit, 6)


def _json(v, default):
    if isinstance(v, str):
        return json.loads(v)
    return default if v is None else v


def _signal(e: dict) -> dict:
    keys = ("event_code", "severity", "temperature_c", "vibration_mm_s", "vibration_g", "power_kw",
            "machine_state", "signal_quality", "inspection_result", "maintenance_status", "days_since_service",
            "note")
    sig = {"source": e["vendor"], "record_id": e["source_record_id"], "kind": e["kind"],
           "event_time": e["event_time"], **{k: e[k] for k in keys if e.get(k) is not None}}
    warnings = _json(e.get("warnings"), [])
    if warnings:
        sig["warnings"] = warnings                   # data-quality caveats travel with the value they qualify
    return sig


def _scope(w: dict) -> set[str]:
    return set(_json(w.get("details"), {}).get("resolution_scope") or [])


def derive_machine_state(asset: dict, events: list[dict], s: Settings) -> dict:
    events = sorted(events, key=lambda e: (e["event_time"], e["source_record_id"]))
    if not events:
        status = "under_maintenance" if asset["asset_status"] == "maintenance" else "unknown"
        return _result(asset, status, 0, [_reason("NO_DATA", 0, "system", None, None, "no processed events")],
                       [], None, None, s)

    latest_all = events[-1]["event_time"]
    sensors = [e for e in events if e["kind"] == "sensor"]
    cutoff = ""                                         # relevance is anchored to the newest SENSOR reading (rule 2)
    if sensors:
        cutoff = timeutil.to_iso(timeutil.from_iso(sensors[-1]["event_time"])
                                 - timedelta(minutes=s.relevance_window_minutes))
    work_orders = [e for e in events if e["kind"] == "work_order"]
    maint = [e for e in events if e["kind"] not in ("sensor", "work_order")]
    reasons: list[dict] = []

    # A work order closed with a passed post-maintenance check resolves earlier findings within its scope (rule 8).
    verified = [w for w in work_orders if w["maintenance_status"] == "closed" and w.get("inspection_result") == "passed"]

    def wo_resolution(code: str, t: str):
        """The latest verified work order that covers this reason code and closed at or after `t`, else None."""
        cat = REASON_CATEGORY.get(code)
        hits = [w for w in verified if cat in _scope(w) and w["event_time"] >= t]
        return hits[-1] if hits else None

    # ---- sensors ---------------------------------------------------------------------------------------
    latest_by_vendor: dict[str, dict] = {}
    for e in sensors:                                   # newest by event time; equal time -> more severe, then id
        cur = latest_by_vendor.get(e["vendor"])
        if cur is None or (e["event_time"], e["severity"] or 0, e["source_record_id"]) > \
                (cur["event_time"], cur["severity"] or 0, cur["source_record_id"]):
            latest_by_vendor[e["vendor"]] = e
    active = []
    for v, e in latest_by_vendor.items():
        if e["event_time"] < cutoff:
            reasons.append(_reason("STALE_SIGNAL_IGNORED", 0, v, e["source_record_id"], e["event_time"],
                                   f"{v} latest reading is more than {s.relevance_window_minutes} min older than "
                                   f"the newest sensor reading for this machine ({sensors[-1]['event_time']})"))
        else:
            active.append(e)

    for e in active:
        src, ref, t = e["vendor"], e["source_record_id"], e["event_time"]
        poor = e.get("signal_quality") == "poor"

        def add(code, lvl, detail, e=e, src=src, ref=ref, t=t, poor=poor):
            if poor and e["event_code"] != "SENSOR_DEGRADED" and lvl > 0:
                lvl -= 1
                detail += " (downgraded one level: poor signal quality)"
            wo = wo_resolution(code, t) if lvl > 0 else None
            if wo:
                lvl, detail = 0, detail + (f" (resolved: work order {wo['source_record_id']} closed {wo['event_time']} "
                                           "with a passed post-maintenance check)")
            reasons.append(_reason(code, lvl, src, ref, t, detail))

        if e["event_code"] in ALERT_CODES:
            add(e["event_code"], e["severity"] or 0,
                f"{src} reported {e['event_code']} with severity {SEVERITY_LABELS[e['severity'] or 0]}")
        temp, vib, kw = e.get("temperature_c"), e.get("vibration_mm_s"), e.get("power_kw")
        if temp is not None and asset["rated_max_temp_c"]:
            lim = asset["rated_max_temp_c"]
            r = _ratio(temp, lim)
            if r >= 1.10:
                add("TEMP_OVER_RATED_LIMIT", 4, f"{temp:.1f}C is {r:.0%} of rated max {lim}C")
            elif r > 1.0:
                add("TEMP_OVER_RATED_LIMIT", 3, f"{temp:.1f}C exceeds rated max {lim}C")
            elif r >= 0.95:
                add("TEMP_NEAR_RATED_LIMIT", 2, f"{temp:.1f}C is {r:.0%} of rated max {lim}C")
        if vib is not None and asset["rated_max_vibration_mm_s"]:
            lim = asset["rated_max_vibration_mm_s"]
            r = _ratio(vib, lim)
            if r >= 1.25:
                add("VIBRATION_OVER_RATED_LIMIT", 4, f"{vib} mm/s is {r:.0%} of rated max {lim}")
            elif r > 1.0:
                add("VIBRATION_OVER_RATED_LIMIT", 3, f"{vib} mm/s exceeds rated max {lim}")
            elif r >= 0.9:
                add("VIBRATION_NEAR_RATED_LIMIT", 2, f"{vib} mm/s is {r:.0%} of rated max {lim}")
        if kw is not None and asset["baseline_power_kw"] and e.get("machine_state") == "running":
            base = asset["baseline_power_kw"]
            dev = _ratio(abs(kw - base), base)
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
    mf_done = completed[-1] if completed else None

    def finding_resolution(code: str, t: str):
        """How a maintenance finding recorded at `t` was resolved: (reason code, explanation) or None.
        A MaintaFlow `completed` maintenance record resolves every earlier finding (it carries no type to scope by);
        a verified work order resolves only findings in its scope."""
        if mf_done and mf_done["event_time"] >= t:
            return ("SUPERSEDED_BY_COMPLETED_MAINTENANCE",
                    f"completed maintenance {mf_done['source_record_id']} ({mf_done['event_time']})")
        wo = wo_resolution(code, t)
        if wo:
            return ("RESOLVED_BY_WORK_ORDER", f"verified work order {wo['source_record_id']} ({wo['event_time']})")
        return None

    def latest(pred):
        c = [e for e in maint if pred(e)]
        return c[-1] if c else None

    def m_add(e, code, lvl, detail, finding=True):
        if e.get("confidence") is not None and e["confidence"] < 0.5 and lvl > 0:
            lvl -= 1
            detail += " (downgraded one level: low manual confidence)"
        if finding:                                      # findings never expire; say how old an open one is
            age_days = (timeutil.from_iso(latest_all) - timeutil.from_iso(e["event_time"])).days
            detail += (f"; unresolved since {e['event_time']}"
                       + (f" ({age_days} days before the newest event; findings stay until resolved)" if age_days else ""))
        reasons.append(_reason(code, lvl, e["vendor"], e["source_record_id"], e["event_time"], detail))

    def resolved_by(newer, older, code, how):
        reasons.append(_reason(code, 0, newer["vendor"], newer["source_record_id"], newer["event_time"],
                               f"{older['source_record_id']} ({older.get('inspection_result')}, {older['event_time']}) "
                               f"resolved by {how} {newer['source_record_id']} ({newer['event_time']})"))

    def superseded(e, what, resolution):
        code, how = resolution
        reasons.append(_reason(code, 0, e["vendor"], e["source_record_id"], e["event_time"],
                               f"{what} resolved by {how}"))

    insp = latest(lambda e: e["kind"] == "inspection")
    insp_code = {"major_defect_found": "INSPECTION_MAJOR_DEFECT", "minor_defect_found": "INSPECTION_MINOR_DEFECT",
                 "passed_with_observation": "INSPECTION_OBSERVATION"}
    insp_res = insp and finding_resolution(insp_code.get(insp.get("inspection_result"), "INSPECTION_MAJOR_DEFECT"),
                                           insp["event_time"])
    if insp and not insp_res:
        res = insp.get("inspection_result")
        if res == "major_defect_found":
            m_add(insp, "INSPECTION_MAJOR_DEFECT", 3, f"major defect: {insp.get('note')}")
        elif res == "minor_defect_found":
            m_add(insp, "INSPECTION_MINOR_DEFECT", 1, f"minor defect: {insp.get('note')}")
        elif res == "passed_with_observation":
            m_add(insp, "INSPECTION_OBSERVATION", 1, f"observation: {insp.get('note')}")
        if res in ("passed", "passed_with_observation"):     # a newer clean inspection resolves an earlier defect
            prior = next((e for e in reversed([i for i in maint if i["kind"] == "inspection" and i is not insp])
                          if e.get("inspection_result") in DEFECT_RESULTS
                          and not finding_resolution(insp_code[e["inspection_result"]], e["event_time"])), None)
            if prior:
                resolved_by(insp, prior, "FINDING_RESOLVED_BY_INSPECTION", f"{res.replace('_', ' ')} inspection")
    elif insp and insp.get("inspection_result") in insp_code:
        superseded(insp, "inspection finding", insp_res)
    ms = latest(lambda e: e.get("maintenance_status") not in (None, "unknown"))
    ms_wo = ms and ms["maintenance_status"] in ("overdue", "due_soon") and wo_resolution("MAINTENANCE_OVERDUE",
                                                                                          ms["event_time"])
    if ms_wo:
        superseded(ms, f"service status '{ms['maintenance_status']}'",
                   ("RESOLVED_BY_WORK_ORDER", f"verified work order {ms_wo['source_record_id']} ({ms_wo['event_time']})"))
    elif ms and ms["maintenance_status"] == "overdue":
        m_add(ms, "MAINTENANCE_OVERDUE", 2, f"service overdue ({ms.get('days_since_service')} days since last)",
              finding=False)
    elif ms and ms["maintenance_status"] == "due_soon":
        m_add(ms, "MAINTENANCE_DUE_SOON", 1, f"service due soon ({ms.get('days_since_service')} days since last)",
              finding=False)
    cal = latest(lambda e: e["kind"] == "calibration")
    cal_res = cal and finding_resolution("CALIBRATION_PENDING", cal["event_time"])
    if cal and cal.get("inspection_result") == "calibration_pending":
        if cal_res:
            superseded(cal, "pending calibration", cal_res)
        else:
            m_add(cal, "CALIBRATION_PENDING", 1, f"calibration pending: {cal.get('note')}")
    elif cal and not cal_res:                               # a newer, non-pending calibration resolves a pending one
        prior = next((e for e in reversed([c for c in maint if c["kind"] == "calibration" and c is not cal])
                      if e.get("inspection_result") == "calibration_pending"
                      and not finding_resolution("CALIBRATION_PENDING", e["event_time"])), None)
        if prior:
            resolved_by(cal, prior, "FINDING_RESOLVED_BY_CALIBRATION", "calibration record")
    note = latest(lambda e: e["kind"] == "operator_note")
    note_lvl = 0
    if note and note.get("note"):
        txt = note["note"].lower()
        note_lvl = max((l for l, ks in NOTE_KEYWORDS.items() if any(k in txt for k in ks)), default=0)
    note_res = note and note_lvl and finding_resolution("OPERATOR_REPORTED_ISSUE", note["event_time"])
    if note_res:
        superseded(note, "operator-reported issue", note_res)
    elif note_lvl:
        m_add(note, "OPERATOR_REPORTED_ISSUE", note_lvl, f"operator note: {note['note']}")

    # ---- CMMS work orders ------------------------------------------------------------------------------------
    for w in work_orders:
        wid, st, check = w["source_record_id"], w["maintenance_status"], w.get("inspection_result")
        scope = _scope(w)
        # a later verified work order covering at least the same scope settles an earlier unsuccessful one
        settled = any(v["event_time"] > w["event_time"] and scope and scope <= _scope(v) for v in verified)
        if st in WO_OPEN_STATES:
            reasons.append(_reason("WORK_ORDER_OPEN", 0, w["vendor"], wid, w["event_time"],
                                   f"work order {wid} is {st.replace('_', ' ')} - {w.get('note')}; attention "
                                   "acknowledged, not resolved until closed with a passed check"))
        elif st == "closed" and check == "passed":
            covers = ", ".join(sorted(scope)) if scope else "nothing (maintenance type has no defined scope)"
            reasons.append(_reason("WORK_ORDER_VERIFIED", 0, w["vendor"], wid, w["event_time"],
                                   f"work order {wid} closed with a passed post-maintenance check - {w.get('note')}; "
                                   f"resolves earlier findings in: {covers}"))
        elif st == "closed" and check == "failed" and not settled:
            reasons.append(_reason("POST_MAINTENANCE_CHECK_FAILED", 2, w["vendor"], wid, w["event_time"],
                                   f"work order {wid} closed but the post-maintenance check failed - {w.get('note')}"))
        elif st == "closed" and check != "passed" and not settled:
            reasons.append(_reason("POST_MAINTENANCE_CHECK_PENDING", 1, w["vendor"], wid, w["event_time"],
                                   f"work order {wid} closed without a passed post-maintenance check yet - {w.get('note')}"))

    # ---- aggregate ---------------------------------------------------------------------------------------
    level = max((r["level"] for r in reasons), default=0)
    high_sources = {r["source"] for r in reasons if r["level"] >= 3 and r["source"] != "system"}
    if len(high_sources) >= 2 and level < 4:
        level += 1
        reasons.append(_reason("MULTI_SOURCE_CORROBORATION", level, "system", None, None,
                               f"independent sources {sorted(high_sources)} both report high severity"))
    status = STATUS_BY_LEVEL[level]
    if asset["asset_status"] == "maintenance":
        # The reference says the asset is in maintenance: that is its status, but attention is still computed from the
        # evidence (needs_attention stays true for a level >= 2) so a real fault is not hidden behind the label.
        status = "under_maintenance"
        reasons.append(_reason("ASSET_UNDER_MAINTENANCE", 0, "reference", None, None,
                               "asset_status=maintenance in the asset reference; attention level still reflects "
                               "the evidence"))
    elif asset["asset_status"] not in ("active", None):
        status = "offline"

    signals = [_signal(e) for e in active]
    for pred in (lambda e: e["kind"] == "inspection", lambda e: e.get("maintenance_status") not in (None, "unknown"),
                 lambda e: e["kind"] == "calibration", lambda e: e["kind"] == "operator_note"):
        e = latest(pred)
        if e and _signal(e) not in signals:
            signals.append(_signal(e))
    for w in work_orders:                               # open + verified work orders, and the latest, are context
        if w["maintenance_status"] in WO_OPEN_STATES or w in verified or w is work_orders[-1]:
            signals.append(_signal(w))
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
