"""Circuit analysis schemas.

The schemas deliberately separate *where a fact came from* (``Provenance``) from
*how trustworthy the connectivity is* (``Verification``):

* ``image_inference``   – read from a schematic image by a vision model (never "verified").
* ``parsed_netlist``    – parsed from a structured netlist (the only source of "verified" nets).
* ``sample_annotation`` – bounding boxes authored together with a bundled sample schematic.
* ``datasheet``         – retrieved from the local datasheet / reference collection.
* ``simulation``        – produced by an actual ngspice run.
* ``calculation``       – deterministic Python calculation.
* ``user_measurement``  – a value the user reported measuring.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Provenance = Literal[
    "image_inference",
    "parsed_netlist",
    "sample_annotation",
    "datasheet",
    "simulation",
    "calculation",
    "user_measurement",
]
Verification = Literal["verified", "inferred"]
ConnectivityStatus = Literal["verified", "inferred", "unavailable"]
ProviderModeLiteral = Literal["demo", "bedrock"]

ComponentType = Literal[
    "resistor",
    "potentiometer",
    "capacitor",
    "inductor",
    "diode",
    "led",
    "zener",
    "transistor",
    "mosfet",
    "opamp",
    "ic",
    "voltage_source",
    "current_source",
    "battery",
    "connector",
    "switch",
    "fuse",
    "crystal",
    "ground",
    "other",
]

REF_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,15}$")


class BoundingBox(BaseModel):
    """Normalised (0..1) bounding box relative to the rendered preview page."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)
    page: int = Field(default=1, ge=1)
    source: Literal["sample_annotation", "image_inference"]

    @model_validator(mode="after")
    def _inside_page(self) -> "BoundingBox":
        if self.x + self.width > 1.0001 or self.y + self.height > 1.0001:
            raise ValueError("bounding box extends beyond the page")
        return self


class ComponentPin(BaseModel):
    pin: str = Field(min_length=1, max_length=16)
    name: str | None = Field(default=None, max_length=32)
    net: str | None = Field(default=None, max_length=64)


class Component(BaseModel):
    ref: str = Field(description="Reference designator, e.g. R1")
    type: ComponentType
    value: str | None = Field(default=None, max_length=64, description="Value as marked/visible; never invented")
    part_number: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: Provenance
    pins: list[ComponentPin] = Field(default_factory=list)
    bbox: BoundingBox | None = None

    @field_validator("ref")
    @classmethod
    def _valid_ref(cls, v: str) -> str:
        v = v.strip()
        if not REF_PATTERN.match(v):
            raise ValueError(f"invalid reference designator: {v!r}")
        return v.upper()


class NetNode(BaseModel):
    ref: str
    pin: str
    pin_name: str | None = None


class Net(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    nodes: list[NetNode]
    verification: Verification
    provenance: Provenance
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _verified_needs_netlist(self) -> "Net":
        if self.verification == "verified" and self.provenance != "parsed_netlist":
            raise ValueError("only nets parsed from a netlist can be marked verified")
        return self


class Evidence(BaseModel):
    kind: Provenance
    detail: str
    source: str | None = None


class PotentialIssue(BaseModel):
    id: str
    title: str
    severity: Literal["info", "warning", "critical"]
    status: Literal["confirmed", "hypothesis"]
    description: str
    evidence: list[Evidence] = Field(default_factory=list)
    related_components: list[str] = Field(default_factory=list)


class DiagnosticAction(BaseModel):
    action: str
    reason: str
    target: str | None = None
    expected: str | None = None
    safety_note: str | None = None


class Uncertainty(BaseModel):
    topic: str
    detail: str


class QuantityValue(BaseModel):
    value: float
    unit: str
    display: str


class CalculationResult(BaseModel):
    calculation: str
    formula: str
    inputs: dict[str, QuantityValue]
    outputs: dict[str, QuantityValue]
    assumptions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provenance: Literal["calculation"] = "calculation"


class PreviewInfo(BaseModel):
    url: str
    width: int
    height: int
    page: int = 1
    page_count: int = 1


class CircuitAnalysis(BaseModel):
    circuit_id: str
    created_at: datetime
    mode: ProviderModeLiteral
    source_kind: Literal["image", "pdf", "netlist", "sample"]
    source_filename: str | None = None
    sample_id: str | None = None
    title: str
    circuit_type: str
    summary: str
    expected_behavior: str | None = None
    components: list[Component] = Field(default_factory=list)
    nets: list[Net] = Field(default_factory=list)
    connectivity_status: ConnectivityStatus
    provenance: list[Provenance] = Field(default_factory=list)
    uncertainties: list[Uncertainty] = Field(default_factory=list)
    issues: list[PotentialIssue] = Field(default_factory=list)
    calculations: list[CalculationResult] = Field(default_factory=list)
    component_roles: dict[str, str] = Field(
        default_factory=dict, description="Roles derived by rule from verified connectivity only"
    )
    next_action: DiagnosticAction | None = None
    preview: PreviewInfo | None = None
    notices: list[str] = Field(default_factory=list, description="Mode labels and other banners for the UI")


QuantityKind = Literal["voltage", "current", "resistance", "frequency"]


class MeasurementCreate(BaseModel):
    quantity: QuantityKind
    value: float
    unit: str = Field(min_length=1, max_length=8)
    target: str = Field(min_length=1, max_length=64, description="Net name, REF, or REF.PIN that was measured")
    reference: str | None = Field(default=None, max_length=64, description="Reference point, e.g. GND")
    power_state: Literal["powered", "unpowered", "unknown"] = "unknown"
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("value must be a finite number")
        return v


class Measurement(MeasurementCreate):
    id: str
    circuit_id: str
    recorded_at: datetime
    si_value: float
    si_unit: str
    source: Literal["user"] = "user"


class TraceRequest(BaseModel):
    target: str = Field(min_length=1, max_length=64, description="Component ref (R1), pin (U1.6) or net name (VOUT)")
    to: str | None = Field(default=None, max_length=64, description="Optional second target to find a path to")


class TracedConnection(BaseModel):
    ref: str
    pin: str
    pin_name: str | None = None
    component_type: str | None = None
    value: str | None = None


class TracedNet(BaseModel):
    net: str
    via_pin: str | None = None
    via_pin_name: str | None = None
    connections: list[TracedConnection]


class TraceResult(BaseModel):
    target: str
    kind: Literal["component", "pin", "net", "unknown"]
    verified: bool
    provenance: Provenance | None = None
    nets: list[TracedNet] = Field(default_factory=list)
    path: list[str] | None = None
    path_description: str | None = None
    uncertainty: str | None = None
    inferred_hint: list[TracedNet] = Field(
        default_factory=list, description="Image-inferred connections. NOT verified; shown only as hints."
    )
