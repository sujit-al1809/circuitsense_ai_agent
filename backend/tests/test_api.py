from __future__ import annotations

import io

import pymupdf

from tests.conftest import load_sample, make_settings, png_bytes, sample_file
from app.main import create_app
from app.services.container import build_services
from fastapi.testclient import TestClient


# --------------------------------------------------------------------------- health / provider


def test_health_demo_mode(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["provider"]["mode"] == "demo"
    assert body["provider"]["configured"] is True
    assert body["sample_circuits"] == 3
    assert body["datasheet_documents"] >= 5
    assert body["simulation_available"] is False
    assert body["storage"] == "in-memory"


def test_health_reports_misconfigured_bedrock():
    settings = make_settings(provider_mode="bedrock", aws_region=None, bedrock_text_model_id=None)
    client = TestClient(create_app(settings, build_services(settings)))
    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["provider"]["configured"] is False
    assert any("AWS_REGION" in i for i in body["provider"]["issues"])
    assert any("BEDROCK_TEXT_MODEL_ID" in i for i in body["provider"]["issues"])
    # Chat must refuse rather than silently fall back to demo answers.
    cid = load_sample(client, "led_basic")
    r = client.post("/api/chat", json={"circuit_id": cid, "message": "Explain this circuit."})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "provider_not_configured"


def test_list_samples(client):
    samples = {s["id"]: s for s in client.get("/api/samples").json()}
    assert set(samples) == {"led_basic", "resistor_divider", "noninverting_amp"}
    assert samples["led_basic"]["circuit_type"] == "led_indicator"
    assert samples["noninverting_amp"]["circuit_type"] == "non_inverting_amplifier"


def test_unknown_sample_404(client):
    r = client.post("/api/samples/nope/load")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "sample_not_found"


# --------------------------------------------------------------------------- uploads


def _upload(client, data: bytes, name="file.png", ctype="image/png", **extra):
    files = {"file": (name, data, ctype)}
    files.update(extra)
    return client.post("/api/circuits/analyze", files=files)


def test_rejects_unsupported_file_type(client):
    r = _upload(client, b"MZ\x90\x00 this is not a schematic", name="evil.exe", ctype="application/octet-stream")
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_media_type"


def test_rejects_file_with_misleading_extension(client):
    r = _upload(client, b"<html><script>alert(1)</script></html>", name="schematic.png")
    assert r.status_code == 415


def test_rejects_empty_file(client):
    r = _upload(client, b"", name="empty.png")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "empty_file"


def test_rejects_oversized_file():
    settings = make_settings(max_upload_mb=0.01)
    client = TestClient(create_app(settings, build_services(settings)))
    r = _upload(client, png_bytes(size=(400, 400)) + b"\x00" * 20_000)
    assert r.status_code == 413


def test_rejects_corrupt_png(client):
    r = _upload(client, b"\x89PNG\r\n\x1a\n" + b"garbage" * 10)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_upload"


def test_unknown_image_in_demo_mode_is_honest(client):
    r = _upload(client, png_bytes())
    assert r.status_code == 201, r.text
    a = r.json()["analysis"]
    assert a["mode"] == "demo"
    assert a["components"] == []
    assert a["connectivity_status"] == "unavailable"
    assert any("Image analysis unavailable" == u["topic"] for u in a["uncertainties"])
    assert a["preview"]["url"].endswith("/preview")
    preview = client.get(a["preview"]["url"])
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"


def test_sample_png_upload_matches_reference_in_demo(client):
    data = sample_file("led_basic", "schematic.png").read_bytes()
    a = _upload(client, data, name="my_led.png").json()["analysis"]
    assert a["connectivity_status"] == "verified"
    assert a["sample_id"] == "led_basic"
    assert any("exact file match" in n for n in a["notices"])
    refs = {c["ref"]: c for c in a["components"]}
    assert set(refs) == {"V1", "R1", "D1"}
    assert all(c["bbox"]["source"] == "sample_annotation" for c in refs.values())


def test_sample_pdf_upload(client):
    data = sample_file("led_basic", "schematic.pdf").read_bytes()
    r = _upload(client, data, name="led.pdf", ctype="application/pdf")
    assert r.status_code == 201, r.text
    a = r.json()["analysis"]
    assert a["source_kind"] == "pdf"
    assert a["connectivity_status"] == "verified"
    assert a["preview"]["page_count"] == 1
    r1 = next(c for c in a["components"] if c["ref"] == "R1")
    assert 0 < r1["bbox"]["x"] < 1


def test_pdf_page_out_of_range(client):
    doc = pymupdf.open()
    doc.new_page()
    pdf = doc.tobytes()
    r = client.post("/api/circuits/analyze", files={"file": ("x.pdf", pdf, "application/pdf")}, data={"page": "3"})
    assert r.status_code == 400
    assert "Page 3" in r.json()["error"]["message"]


def test_netlist_only_upload(client):
    data = sample_file("resistor_divider", "resistor_divider.net").read_bytes()
    a = _upload(client, data, name="div.net", ctype="text/plain").json()["analysis"]
    assert a["source_kind"] == "netlist"
    assert a["connectivity_status"] == "verified"
    assert a["circuit_type"] == "resistor_divider"
    assert a["preview"] is None


def test_image_plus_netlist_attachment(client):
    net = sample_file("noninverting_amp", "noninverting_amp.net").read_bytes()
    r = _upload(client, png_bytes(), netlist=("amp.net", net, "text/plain"))
    a = r.json()["analysis"]
    assert a["connectivity_status"] == "verified"
    assert a["circuit_type"] == "non_inverting_amplifier"
    assert "parsed_netlist" in a["provenance"]


def test_invalid_netlist_rejected(client):
    r = _upload(client, b"(export (version E) (components (comp (ref R1)) (nets (net (name X) (node (ref Q9) (pin 1)))))", name="bad.net")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_netlist"


# --------------------------------------------------------------------------- session endpoints


def test_get_circuit_and_unknown_id(client):
    cid = load_sample(client, "led_basic")
    r = client.get(f"/api/circuits/{cid}")
    assert r.status_code == 200
    assert r.json()["analysis"]["circuit_id"] == cid
    assert client.get("/api/circuits/cir_missing").status_code == 404


def test_trace_endpoint_verified(client):
    cid = load_sample(client, "noninverting_amp")
    r = client.post(f"/api/circuits/{cid}/trace", json={"target": "U1.OUT", "to": "U1.IN-"})
    body = r.json()
    assert body["verified"] is True
    assert body["path_description"] == "U1.6 (OUT) → net VOUT → R2.2 → R2 [10k] → R2.1 → net FB → U1.2 (IN-)"


def test_trace_endpoint_without_connectivity(client):
    cid = _upload(client, png_bytes()).json()["analysis"]["circuit_id"]
    body = client.post(f"/api/circuits/{cid}/trace", json={"target": "R1"}).json()
    assert body["verified"] is False
    assert "unavailable" in body["uncertainty"].lower()
    assert body["nets"] == []


def test_measurements_endpoint(client):
    cid = load_sample(client, "led_basic")
    r = client.post(f"/api/circuits/{cid}/measurements", json={"quantity": "voltage", "value": 4.9, "unit": "V", "target": "VCC", "reference": "GND", "power_state": "powered"})
    assert r.status_code == 201
    m = r.json()
    assert m["si_value"] == 4.9 and m["si_unit"] == "V" and m["source"] == "user"
    r = client.post(f"/api/circuits/{cid}/measurements", json={"quantity": "current", "value": 9.1, "unit": "mA", "target": "R1"})
    assert r.json()["si_value"] == 9.1e-3
    bad = client.post(f"/api/circuits/{cid}/measurements", json={"quantity": "voltage", "value": 1, "unit": "mA", "target": "VCC"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_measurement"
    assert len(client.get(f"/api/circuits/{cid}/measurements").json()) == 2
    # The analysis session view includes the measurements.
    assert len(client.get(f"/api/circuits/{cid}").json()["measurements"]) == 2


def test_chat_validation_and_history(client):
    assert client.post("/api/chat", json={"circuit_id": "cir_missing", "message": "hi"}).status_code == 404
    cid = load_sample(client, "led_basic")
    assert client.post("/api/chat", json={"circuit_id": cid, "message": ""}).status_code == 422
    bad_focus = client.post("/api/chat", json={"circuit_id": cid, "message": "why?", "focus_component": "Q7"})
    assert bad_focus.status_code == 422
    r = client.post("/api/chat", json={"circuit_id": cid, "message": "Why is this resistor needed?", "focus_component": "R1"})
    body = r.json()
    assert body["focus_component"] == "R1"
    assert "current-limiting" in body["answer"]
    assert body["mode"] == "demo" and "Demo mode" in body["notice"]
    history = client.get(f"/api/circuits/{cid}").json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["response"]["message_id"] == body["message_id"]


def test_s3_archive_used_when_configured():
    class FakeS3:
        def __init__(self):
            self.calls = []

        def put_object(self, **kwargs):
            self.calls.append(kwargs)

    fake = FakeS3()
    settings = make_settings(s3_bucket="my-bucket", aws_region="us-east-1")
    client = TestClient(create_app(settings, build_services(settings, s3_client=fake)))
    r = _upload(client, png_bytes(), name="../../etc/passwd.png")
    assert r.status_code == 201
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["Bucket"] == "my-bucket"
    assert call["ServerSideEncryption"] == "AES256"
    parts = call["Key"].split("/")
    assert parts[:2] == ["circuitsense", "uploads"] and parts[2].startswith("cir_")
    assert len(parts) == 4, "the user-supplied filename must not add path segments"


def test_body_size_middleware():
    settings = make_settings(max_upload_mb=0.01)
    client = TestClient(create_app(settings, build_services(settings)))
    r = client.post("/api/circuits/analyze", content=b"x" * 2_200_000, headers={"content-type": "application/octet-stream"})
    assert r.status_code == 413


def test_serves_built_frontend_without_path_traversal(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>CircuitSense</title>", encoding="utf-8")
    (dist / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    settings = make_settings(frontend_dist=dist)
    client = TestClient(create_app(settings, build_services(settings)))
    assert "CircuitSense" in client.get("/").text
    assert client.get("/app.js").text == "console.log(1)"
    assert "CircuitSense" in client.get("/some/client/route").text
    assert "TOP SECRET" not in client.get("/..%2fsecret.txt").text
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
