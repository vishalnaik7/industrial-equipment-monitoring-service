"""Canonical vocabularies and vendor -> canonical enum maps. Changing a mapping here is a data-contract change."""
from __future__ import annotations

# ---- canonical -------------------------------------------------------------------------------------------
SEVERITY = {"info": 0, "none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
SEVERITY_LABELS = ["none", "low", "medium", "high", "critical"]
ALERT_CODES = {"HIGH_VIBRATION", "HIGH_TEMPERATURE", "POWER_ANOMALY", "SENSOR_DEGRADED", "UNKNOWN_ALERT"}
CLEAR_CODES = {"RECOVERY", "NOMINAL"}
DEFAULT_SEVERITY = {"HIGH_VIBRATION": 2, "HIGH_TEMPERATURE": 2, "POWER_ANOMALY": 2, "SENSOR_DEGRADED": 1,
                    "UNKNOWN_ALERT": 1, "RECOVERY": 0, "NOMINAL": 0}
MACHINE_STATES = {"running", "idle", "stopped", "fault"}

# ---- PulseForge ------------------------------------------------------------------------------------------
PF_EVENT_MAP = {"HIGH_VIBRATION": "HIGH_VIBRATION", "TEMP_SPIKE": "HIGH_TEMPERATURE",
                "SENSOR_HEALTH_DROP": "SENSOR_DEGRADED", "POWER_FLUCTUATION": "POWER_ANOMALY",
                "RECOVERY_SIGNAL": "RECOVERY"}

# ---- ThermexWatch ----------------------------------------------------------------------------------------
TW_ALERT_MAP = {"VIB_WARN": "HIGH_VIBRATION", "TEMP_WARN": "HIGH_TEMPERATURE", "TEMP_CRIT": "HIGH_TEMPERATURE",
                "POWER_DROP": "POWER_ANOMALY", "OK": "NOMINAL"}
TW_LEVEL_SEVERITY = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}          # vendor level 1..5 -> canonical 0..4
# Used only when `level` is missing/invalid: the alert code itself still carries severity (TEMP_CRIT is critical).
TW_ALERT_DEFAULT_SEVERITY = {"VIB_WARN": 2, "TEMP_WARN": 2, "TEMP_CRIT": 4, "POWER_DROP": 2, "OK": 0}
TW_QUALITY = {"GOOD": "good", "FAIR": "fair", "POOR": "poor"}

# ---- MaintaFlow ------------------------------------------------------------------------------------------
MF_RECORD_KINDS = {"inspection", "maintenance_update", "operator_note", "calibration"}
MF_INSPECTION = {"passed", "passed_with_observation", "minor_defect_found", "major_defect_found",
                 "calibration_pending"}
MF_MAINT_STATUS = {"not_due", "due_soon", "overdue", "completed", "unknown"}
MF_CONFIDENCE = {"low": 0.4, "medium": 0.7, "high": 0.9}

# ---- CMMS work orders (internal) -------------------------------------------------------------------------
WO_STATUS = {"open", "in_progress", "reopened", "closed", "cancelled"}
WO_OPEN_STATES = {"open", "in_progress", "reopened"}

# Which reason categories a work order can resolve (rule 8). A repair only clears findings it plausibly addressed:
# a power check does not clear a vibration alert. Unknown maintenance types resolve nothing (warning on the record).
WO_TYPE_SCOPE = {
    "power_check": {"power"},
    "cooling_system": {"temperature"},
    "heat_shield": {"temperature"},
    "sensor_recalibration": {"sensor", "calibration"},
    "vibration_check": {"vibration"}, "bearing_replacement": {"vibration"}, "belt_replacement": {"vibration"},
    "general_service": {"vibration", "temperature", "power", "sensor", "calibration", "inspection", "operator",
                        "service", "unknown"},
}
# What triggered the work order is also addressed by it (the inspection finding / operator report that opened it).
WO_TRIGGER_SCOPE = {"inspection": {"inspection"}, "operator_report": {"operator"}, "scheduled_service": {"service"},
                    "sensor_alert": set()}
# Reason code -> category (used for work-order scope).
REASON_CATEGORY = {
    "HIGH_VIBRATION": "vibration", "VIBRATION_OVER_RATED_LIMIT": "vibration", "VIBRATION_NEAR_RATED_LIMIT": "vibration",
    "HIGH_TEMPERATURE": "temperature", "TEMP_OVER_RATED_LIMIT": "temperature", "TEMP_NEAR_RATED_LIMIT": "temperature",
    "POWER_ANOMALY": "power", "POWER_DEVIATION": "power", "SENSOR_DEGRADED": "sensor", "UNKNOWN_ALERT": "unknown",
    "INSPECTION_MAJOR_DEFECT": "inspection", "INSPECTION_MINOR_DEFECT": "inspection",
    "INSPECTION_OBSERVATION": "inspection", "CALIBRATION_PENDING": "calibration",
    "OPERATOR_REPORTED_ISSUE": "operator", "MAINTENANCE_OVERDUE": "service", "MAINTENANCE_DUE_SOON": "service",
}
BOOL_TRUE, BOOL_FALSE = {"true", "1", "yes", "y"}, {"false", "0", "no", "n"}
WO_POST_CHECK = {"passed", "failed", "pending"}

# plausibility ranges (outside => treated as sentinel/garbage such as -999)
RANGES = {"temperature_c": (-50.0, 1000.0), "vibration_mm_s": (0.0, 1000.0), "vibration_g": (0.0, 1000.0),
          "power_kw": (0.0, 100000.0), "sensor_health": (0.0, 1.0), "vendor_confidence": (0.0, 1.0),
          "temperature_f": (-58.0, 1832.0), "downtime_minutes": (0.0, 525600.0)}
