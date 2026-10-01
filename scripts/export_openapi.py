import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("WORKER_EMBEDDED", "false")
from app.main import app
out = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=2) + "\n")
print("wrote", out)
