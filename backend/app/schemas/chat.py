from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.circuit import CalculationResult, DiagnosticAction, Measurement, Provenance, ProviderModeLiteral


class ChatRequest(BaseModel):
    circuit_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=4000)
    focus_component: str | None = Field(default=None, max_length=16)


class Citation(BaseModel):
    id: str
    source: str = Field(description="File name inside the local datasheet collection")
    title: str
    page: int | None = None
    section: str | None = None
    excerpt: str
    illustrative: bool = Field(description="True when the document is illustrative teaching data, not a manufacturer datasheet")
    score: float


class Finding(BaseModel):
    statement: str
    basis: Provenance
    detail: str | None = None


class ToolTrace(BaseModel):
    name: str
    input: dict[str, Any]
    status: Literal["ok", "error", "unavailable"]
    summary: str
    duration_ms: float


StopReason = Literal["final", "max_iterations", "tool_failures", "provider_error"]


class ChatResponse(BaseModel):
    message_id: str
    circuit_id: str
    created_at: datetime
    mode: ProviderModeLiteral
    answer: str
    confirmed: list[Finding] = Field(default_factory=list)
    hypotheses: list[Finding] = Field(default_factory=list)
    next_step: DiagnosticAction | None = None
    follow_up_questions: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    calculations: list[CalculationResult] = Field(default_factory=list)
    measurements_recorded: list[Measurement] = Field(default_factory=list)
    tool_calls: list[ToolTrace] = Field(default_factory=list)
    iterations: int
    stopped_reason: StopReason
    focus_component: str | None = None
    warnings: list[str] = Field(default_factory=list)
    notice: str | None = None


class ChatMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    focus_component: str | None = None
    response: ChatResponse | None = None
