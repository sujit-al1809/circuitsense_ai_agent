"""Capture real demo-mode API responses as frontend test fixtures.

Run from the backend directory:  python -m scripts.export_frontend_fixtures
Writes frontend/src/test/fixtures.json.
"""

from __future__ import annotations

import json
import warnings

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import REPO_ROOT, Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def main() -> None:
    client = TestClient(create_app(Settings(_env_file=None, provider_mode="demo", ngspice_path="ngspice-not-installed")))
    session = client.post("/api/samples/led_basic/load").json()
    cid = session["analysis"]["circuit_id"]
    fixtures = {
        "health": client.get("/health").json(),
        "samples": client.get("/api/samples").json(),
        "session": session,
        "traceR1": client.post(f"/api/circuits/{cid}/trace", json={"target": "R1"}).json(),
        "chatFocus": client.post("/api/chat", json={"circuit_id": cid, "message": "Why is R1 needed?", "focus_component": "R1"}).json(),
        "chatDebug": client.post("/api/chat", json={"circuit_id": cid, "message": "What should I measure next?"}).json(),
    }
    out = REPO_ROOT / "frontend" / "src" / "test" / "fixtures.json"
    out.write_text(json.dumps(fixtures, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
