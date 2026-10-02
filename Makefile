.PHONY: install run worker ui test test-backend test-frontend seed openapi up
install:        ; cd backend && pip install -r requirements-dev.txt && cd ../frontend && npm install
run:            ; cd backend && uvicorn app.main:app --reload    # API :8000 with embedded worker thread
worker:         ; cd backend && WORKER_EMBEDDED=false python -m app.worker
ui:             ; cd frontend && npm run dev                      # React dashboard :5173 (proxies the API on :8000)
test:           test-backend test-frontend
test-backend:   ; cd backend && python -m pytest -q
test-frontend:  ; cd frontend && npm run typecheck && npm test
seed:           ; cd backend && python scripts/load_seed.py
openapi:        ; cd backend && python scripts/export_openapi.py  # writes docs/openapi.json
up:             ; docker compose up --build                       # api :8000, dashboard :8080
