"""Background processing. `runner` holds the claim -> normalize -> derive cycle; run standalone with
`python -m app.worker` or embedded as a thread in the API process (WORKER_EMBEDDED=true)."""
from .runner import run_once, worker_loop

__all__ = ["run_once", "worker_loop"]
