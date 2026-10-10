"""Vercel Python entrypoint: exposes the CircuitSense FastAPI app as a serverless function.

Vercel's FastAPI preset sends every route to this app. The build copies the React UI into
``public/`` (served from the CDN); the app also serves it as a fallback for ``/``.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DATA_DIR", str(ROOT / "data"))
if (ROOT / "public" / "index.html").is_file():
    os.environ.setdefault("FRONTEND_DIST", str(ROOT / "public"))

from app.main import app  # noqa: E402,F401
