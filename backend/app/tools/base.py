from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel

from app.config import Settings
from app.schemas.chat import Citation
from app.schemas.circuit import CalculationResult, Measurement
from app.services.circuit_graph import CircuitGraph
from app.services.diagnostics import CircuitInsight
from app.services.retrieval_service import RetrievalBackend
from app.services.simulation_service import SimulationService
from app.services.storage import Session

ToolStatus = Literal["ok", "error", "unavailable"]


def simplify_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Reduce a Pydantic JSON schema to the plain subset every Bedrock model accepts.

    Removes titles, inlines ``$ref``/``$defs``, turns ``anyOf: [X, {"type": "null"}]`` into
    ``X`` and multi-type lists into their first non-null type. Optional fields stay optional
    because they are not listed in ``required``; tool inputs are re-validated with Pydantic.
    """
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(defs[node["$ref"].split("/")[-1]])
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            if len(options) == 1:
                merged = {**{k: v for k, v in node.items() if k != "anyOf"}, **options[0]}
                return walk(merged)
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in ("title", "$defs"):
                continue
            if key == "default" and value is None:
                continue
            if key == "type" and isinstance(value, list):
                value = next((t for t in value if t != "null"), "string")
            if key == "const":
                out["enum"] = [value]
                continue
            out[key] = value if key in ("enum", "required") else walk(value)
        return out

    return walk(schema)


@dataclass
class ToolContext:
    session: Session
    settings: Settings
    retrieval: RetrievalBackend
    simulation: SimulationService
    graph: CircuitGraph | None
    insight: CircuitInsight | None
    user_message: str
    record_measurement: Callable[[Session, Any], Measurement]


@dataclass
class ToolOutput:
    status: ToolStatus
    data: dict[str, Any]
    summary: str
    citations: list[Citation] = field(default_factory=list)
    calculations: list[CalculationResult] = field(default_factory=list)
    measurements: list[Measurement] = field(default_factory=list)
    simulation_ran: bool = False


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], ToolOutput]

    def json_schema(self) -> dict[str, Any]:
        return simplify_schema(self.input_model.model_json_schema())

    def bedrock_spec(self) -> dict[str, Any]:
        return {"toolSpec": {"name": self.name, "description": self.description, "inputSchema": {"json": self.json_schema()}}}
