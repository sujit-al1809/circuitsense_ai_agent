from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.responses import Response

from app.api.errors import ApiError
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse
from app.schemas.circuit import Measurement, MeasurementCreate, TraceRequest, TraceResult
from app.schemas.common import CircuitSession, ErrorResponse, HealthResponse, ProviderStatus, SampleCircuitInfo
from app.services.analysis_service import UploadedFile
from app.services.bedrock_service import ProviderError
from app.services.circuit_graph import CircuitGraph, trace_unverified
from app.services.container import Services
from app.services.storage import Session
from app.services.units import UnitError
from app.services.vision_service import UploadValidationError

VERSION = "0.1.0"
router = APIRouter()
ERRORS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}


def get_services(request: Request) -> Services:
    return request.app.state.services


ServicesDep = Annotated[Services, Depends(get_services)]


def _session(services: Services, circuit_id: str) -> Session:
    session = services.store.get(circuit_id)
    if session is None:
        raise ApiError(404, "circuit_not_found", f"No analysis session '{circuit_id}'. Sessions are kept in memory and are lost on restart.")
    return session


def _session_view(session: Session) -> CircuitSession:
    return CircuitSession(analysis=session.analysis, messages=session.messages, measurements=session.measurements)


async def _read_upload(upload: UploadFile, limit: int) -> UploadedFile:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise ApiError(413, "file_too_large", f"'{upload.filename}' exceeds the {limit / 1_048_576:g} MB upload limit.")
    return UploadedFile(filename=upload.filename or "upload", data=data, content_type=upload.content_type)


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(services: ServicesDep) -> HealthResponse:
    provider = services.provider_info()
    return HealthResponse(
        status="ok" if provider.configured else "degraded",
        app=services.settings.app_name,
        version=VERSION,
        provider=provider,
        simulation_available=services.simulation.available,
        datasheet_documents=services.retrieval.document_count,
        sample_circuits=len(services.samples.list()),
        storage=services.store.name,
    )


@router.get("/api/provider/status", response_model=ProviderStatus, tags=["system"])
def provider_status(services: ServicesDep, probe: bool = Query(False, description="Also send a tiny Converse request (incurs a small cost)")) -> ProviderStatus:
    info = services.provider_info()
    checks = []
    if services.bedrock is not None:
        checks.append(services.bedrock.check_model(services.settings.bedrock_text_model_id, "text", probe=probe))
        checks.append(services.bedrock.check_model(services.settings.effective_vision_model_id, "vision", probe=False))
    return ProviderStatus(provider=info, checks=checks)


@router.get("/api/samples", response_model=list[SampleCircuitInfo], tags=["samples"])
def list_samples(services: ServicesDep) -> list[SampleCircuitInfo]:
    return [s.info() for s in services.samples.list()]


@router.post("/api/samples/{sample_id}/load", response_model=CircuitSession, status_code=status.HTTP_201_CREATED, responses=ERRORS, tags=["samples"])
def load_sample(sample_id: str, services: ServicesDep) -> CircuitSession:
    try:
        session = services.analysis.load_sample(sample_id)
    except KeyError:
        raise ApiError(404, "sample_not_found", f"Unknown sample '{sample_id}'.") from None
    return _session_view(session)


@router.post("/api/circuits/analyze", response_model=CircuitSession, status_code=status.HTTP_201_CREATED, responses=ERRORS, tags=["circuits"])
async def analyze_circuit(
    services: ServicesDep,
    file: Annotated[UploadFile, File(description="Schematic image (PNG/JPEG/WebP), PDF, or KiCad netlist (.net)")],
    netlist: Annotated[UploadFile | None, File(description="Optional KiCad netlist for verified connectivity")] = None,
    page: Annotated[int, Form(ge=1, le=500)] = 1,
) -> CircuitSession:
    limit = services.settings.max_upload_bytes
    upload = await _read_upload(file, limit)
    attached = await _read_upload(netlist, limit) if netlist is not None and netlist.filename else None
    try:
        from starlette.concurrency import run_in_threadpool

        session = await run_in_threadpool(services.analysis.analyze_upload, upload, attached, page)
    except UploadValidationError as exc:
        status_code = 415 if exc.code == "unsupported_media_type" else (413 if exc.code == "file_too_large" else 400)
        raise ApiError(status_code, exc.code, str(exc)) from None
    except ProviderError as exc:
        raise ApiError(502, f"provider_{exc.code}".lower(), str(exc)) from None
    return _session_view(session)


@router.get("/api/circuits/{circuit_id}", response_model=CircuitSession, responses=ERRORS, tags=["circuits"])
def get_circuit(circuit_id: str, services: ServicesDep) -> CircuitSession:
    return _session_view(_session(services, circuit_id))


@router.get("/api/circuits/{circuit_id}/preview", responses={200: {"content": {"image/png": {}}}, **ERRORS}, tags=["circuits"])
def get_preview(circuit_id: str, services: ServicesDep) -> Response:
    session = _session(services, circuit_id)
    if not session.preview_png:
        raise ApiError(404, "no_preview", "This session has no schematic image.")
    return Response(content=session.preview_png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.post("/api/circuits/{circuit_id}/trace", response_model=TraceResult, responses=ERRORS, tags=["circuits"])
def trace(circuit_id: str, body: TraceRequest, services: ServicesDep) -> TraceResult:
    session = _session(services, circuit_id)
    analysis = session.analysis
    if analysis.connectivity_status != "verified":
        return trace_unverified(analysis, body.target)
    return CircuitGraph.from_analysis(analysis).trace(body.target, body.to)


@router.post("/api/circuits/{circuit_id}/measurements", response_model=Measurement, status_code=status.HTTP_201_CREATED, responses=ERRORS, tags=["circuits"])
def add_measurement(circuit_id: str, body: MeasurementCreate, services: ServicesDep) -> Measurement:
    session = _session(services, circuit_id)
    try:
        return services.measurements.record(session, body)
    except (UnitError, ValueError) as exc:
        raise ApiError(422, "invalid_measurement", str(exc)) from None


@router.get("/api/circuits/{circuit_id}/measurements", response_model=list[Measurement], responses=ERRORS, tags=["circuits"])
def list_measurements(circuit_id: str, services: ServicesDep) -> list[Measurement]:
    return _session(services, circuit_id).measurements


@router.post("/api/chat", response_model=ChatResponse, responses=ERRORS, tags=["chat"])
def chat(body: ChatRequest, services: ServicesDep) -> ChatResponse:
    session = _session(services, body.circuit_id)
    if services.orchestrator is None:
        raise ApiError(503, "provider_not_configured", "The AI provider is not configured: " + "; ".join(services.settings.provider_config_issues()))
    focus = body.focus_component.strip().upper() if body.focus_component else None
    if focus and focus not in {c.ref for c in session.analysis.components}:
        raise ApiError(422, "unknown_focus_component", f"{focus} is not a component of this circuit.")
    if not session.lock.acquire(timeout=120):
        raise ApiError(409, "session_busy", "Another question for this circuit is still being answered.")
    try:
        response = services.orchestrator.run(session, body.message.strip(), focus)
        if response.mode == "demo":
            response.notice = "Demo mode — deterministic rule-based answer, not AI model output."
        now = datetime.now(timezone.utc)
        session.messages.append(ChatMessage(id=f"u_{response.message_id}", role="user", content=body.message.strip(), created_at=now, focus_component=focus))
        session.messages.append(ChatMessage(id=response.message_id, role="assistant", content=response.answer, created_at=response.created_at, focus_component=focus, response=response))
        services.store.save(session)
        return response
    finally:
        session.lock.release()
