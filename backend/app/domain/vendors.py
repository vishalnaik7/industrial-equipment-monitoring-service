"""Registry of ingestion sources: three external vendors plus the internal CMMS (maintenance work orders).
Adding a source = one entry here + a normalizer + an envelope schema."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VendorSpec:
    slug: str              # URL segment: /v1/ingest/{slug}
    name: str              # canonical vendor name stored on every record
    records_key: str       # envelope key holding the record list
    id_field: str          # vendor record id (idempotency key)
    machine_field: str     # vendor machine reference (entity resolution input)
    name_field: str        # envelope field where the vendor names itself (checked against the endpoint)
    plant_field: str       # envelope field carrying the vendor's plant/site/factory code
    # Vendor-supplied version of a batch (when the vendor generated it). Orders revisions of the same record; None when
    # the vendor sends no such field (the record's own event time is used instead - see the design note).
    version_field: str | None = None
    version_format: str | None = None       # "iso" | "epoch_ms"


VENDORS: dict[str, VendorSpec] = {v.slug: v for v in (
    VendorSpec("pulseforge", "PulseForge", "events", "event_id", "machine_id", "vendor", "plant_id",
               "batch_generated_at", "iso"),
    VendorSpec("thermexwatch", "ThermexWatch", "readings", "readingId", "assetCode", "source", "site_code",
               "response_time_epoch_ms", "epoch_ms"),
    VendorSpec("maintaflow", "MaintaFlow", "records", "record_id", "machine_ref", "provider_name", "factory_id"),
    VendorSpec("workorders", "CMMS", "work_orders", "work_order_id", "machine_id", "source", "plant_id"),
)}
SCHEMA_VERSION = "v1"


def by_name(name: str) -> VendorSpec | None:
    return next((v for v in VENDORS.values() if v.name == name), None)
