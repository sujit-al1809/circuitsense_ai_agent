from __future__ import annotations

import io
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore", message=".*httpx.*", category=DeprecationWarning)

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.config import REPO_ROOT, Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.circuit_graph import CircuitGraph  # noqa: E402
from app.services.circuit_parser import parse_netlist  # noqa: E402
from app.services.container import build_services  # noqa: E402

SAMPLES = REPO_ROOT / "data" / "sample_circuits"


def make_settings(**overrides) -> Settings:
    base = {"provider_mode": "demo", "ngspice_path": "definitely-not-installed-ngspice"}
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def services(settings):
    return build_services(settings)


@pytest.fixture
def client(settings, services) -> TestClient:
    return TestClient(create_app(settings, services))


def sample_graph(sample_id: str) -> CircuitGraph:
    parsed = parse_netlist((SAMPLES / sample_id / f"{sample_id}.net").read_text(encoding="utf-8"))
    return CircuitGraph(parsed.components, parsed.nets)


def png_bytes(color=(200, 30, 30), size=(64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def load_sample(client: TestClient, sample_id: str) -> str:
    r = client.post(f"/api/samples/{sample_id}/load")
    assert r.status_code == 201, r.text
    return r.json()["analysis"]["circuit_id"]


def sample_file(sample_id: str, name: str) -> Path:
    return SAMPLES / sample_id / name
