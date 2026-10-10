from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.chat import ChatMessage
from app.schemas.circuit import CircuitAnalysis, Measurement


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ProviderInfo(BaseModel):
    mode: Literal["demo", "bedrock"]
    label: str
    configured: bool
    region: str | None = None
    text_model_id: str | None = None
    vision_model_id: str | None = None
    issues: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    app: str
    version: str
    provider: ProviderInfo
    simulation_available: bool
    datasheet_documents: int
    sample_circuits: int
    storage: str


class ModelCheck(BaseModel):
    role: Literal["text", "vision"]
    model_id: str | None
    ok: bool
    detail: str
    input_modalities: list[str] = Field(default_factory=list)


class ProviderStatus(BaseModel):
    provider: ProviderInfo
    checks: list[ModelCheck] = Field(default_factory=list)


class SampleCircuitInfo(BaseModel):
    id: str
    name: str
    description: str
    circuit_type: str
    example_questions: list[str] = Field(default_factory=list)


class CircuitSession(BaseModel):
    analysis: CircuitAnalysis
    messages: list[ChatMessage] = Field(default_factory=list)
    measurements: list[Measurement] = Field(default_factory=list)
