.PHONY: install run worker test seed openapi up
install:   ; pip install -r requirements-dev.txt
run:       ; uvicorn app.main:app --reload            # embedded worker thread (single process)
worker:    ; WORKER_EMBEDDED=false python -m app.worker
test:      ; python -m pytest -q
seed:      ; python scripts/load_seed.py
openapi:   ; python scripts/export_openapi.py
up:        ; docker compose up --build
