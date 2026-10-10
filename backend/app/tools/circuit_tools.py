"""The CircuitSense agent tools.

A. get_component_info          – component details + datasheet passages (with sources)
B. trace_net                   – verified connectivity (explicit uncertainty without a netlist)
C. calculate_electrical_values – deterministic Python calculations
D. record_measurement          – store a measurement the *user* reported
E. simulate_circuit            – ngspice DC operating point (only if installed + verified netlist)
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.chat import Citation
from app.schemas.circuit import MeasurementCreate
from app.services.calculations import CALCULATIONS, CalculationError, calculate
from app.services.circuit_graph import trace_unverified
from app.services.simulation_service import SimulationInputError, SimulationUnavailable
from app.services.units import UnitError, format_quantity
from app.tools.base import Tool, ToolContext, ToolOutput

# --------------------------------------------------------------------------- A


class GetComponentInfoInput(BaseModel):
    ref: str | None = Field(default=None, max_length=16, description="Reference designator, e.g. R1")
    query: str | None = Field(default=None, max_length=300, description="What to look up in the datasheets, e.g. 'maximum forward current'")
    max_passages: int = Field(default=3, ge=1, le=6)

    @model_validator(mode="after")
    def _one_required(self) -> "GetComponentInfoInput":
        if not (self.ref or self.query):
            raise ValueError("provide 'ref' and/or 'query'")
        return self


def get_component_info(ctx: ToolContext, inp: GetComponentInfoInput) -> ToolOutput:
    analysis = ctx.session.analysis
    comp = None
    data: dict[str, Any] = {}
    if inp.ref:
        ref = inp.ref.strip().upper()
        comp = next((c for c in analysis.components if c.ref == ref), None)
        if comp is None:
            known = ", ".join(c.ref for c in analysis.components) or "none"
            return ToolOutput(
                status="error",
                data={"error": f"{ref} is not a component of this circuit", "known_components": known},
                summary=f"{ref} not found (known: {known})",
            )
        data["component"] = {
            "ref": comp.ref,
            "type": comp.type,
            "value": comp.value,
            "description": comp.description,
            "part_number": comp.part_number,
            "provenance": comp.provenance,
            "confidence": comp.confidence,
            "pins": [p.model_dump(exclude_none=True) for p in comp.pins],
        }
        if ctx.insight and comp.ref in ctx.insight.roles:
            data["component"]["role_from_verified_topology"] = ctx.insight.roles[comp.ref]
        elif analysis.connectivity_status != "verified":
            data["component"]["role_note"] = "Role cannot be derived: connectivity is not verified."
    query_parts = [inp.query or ""]
    if comp is not None:
        query_parts.append(comp.type.replace("_", " "))
        if not inp.query:
            query_parts.append("overview characteristics troubleshooting")
    passages = ctx.retrieval.search(
        " ".join(p for p in query_parts if p),
        component_type=comp.type if comp else None,
        part_number=comp.part_number if comp else None,
        k=inp.max_passages,
    )
    citations = [
        Citation(
            id=p.id,
            source=p.source,
            title=p.title,
            page=p.page,
            section=p.section,
            excerpt=p.excerpt[:700],
            illustrative=p.illustrative,
            score=p.score,
        )
        for p in passages
    ]
    data["passages"] = [
        {
            "id": c.id,
            "source": c.source,
            "title": c.title,
            "page": c.page,
            "section": c.section,
            "illustrative": c.illustrative,
            "excerpt": c.excerpt,
        }
        for c in citations
    ]
    if not citations:
        data["retrieval_note"] = "No matching passage was found in the local datasheet collection."
    summary = (f"{comp.ref} ({comp.type}, {comp.value or 'no value'}); " if comp else "") + f"{len(citations)} datasheet passage(s)"
    return ToolOutput(status="ok", data=data, summary=summary, citations=citations)


# --------------------------------------------------------------------------- B


class TraceNetInput(BaseModel):
    target: str = Field(min_length=1, max_length=64, description="Component ref (R1), pin (U1.6 or U1.OUT) or net name (VOUT)")
    to: str | None = Field(default=None, max_length=64, description="Optional second target: return the verified path between the two")


def trace_net(ctx: ToolContext, inp: TraceNetInput) -> ToolOutput:
    if ctx.graph is None or ctx.graph.is_empty:
        result = trace_unverified(ctx.session.analysis, inp.target)
        return ToolOutput(
            status="unavailable",
            data=result.model_dump(exclude_none=True),
            summary="no verified connectivity" + (f"; {len(result.inferred_hint)} inferred hint(s)" if result.inferred_hint else ""),
        )
    result = ctx.graph.trace(inp.target, inp.to)
    if result.kind == "unknown":
        return ToolOutput(status="error", data=result.model_dump(exclude_none=True), summary=result.uncertainty or "unknown target")
    summary = f"{result.target}: " + "; ".join(
        f"{n.net} → {', '.join(f'{c.ref}.{c.pin}' for c in n.connections) or 'nothing else'}" for n in result.nets
    )
    if result.path_description:
        summary = f"path {result.path_description}"
    elif inp.to and result.uncertainty:
        summary += f" (path: {result.uncertainty})"
    return ToolOutput(status="ok", data=result.model_dump(exclude_none=True), summary=summary[:400])


# --------------------------------------------------------------------------- C


class CalculateInput(BaseModel):
    calculation: Literal[tuple(sorted(CALCULATIONS))]  # type: ignore[valid-type]
    params: dict[str, Any] = Field(
        description=(
            "Inputs as numbers (SI) or strings with units. ohms_law: two of voltage/current/resistance. "
            "voltage_divider: vin, r_top, r_bottom, optional r_load. led_series_resistor: supply_voltage, "
            "forward_voltage, resistance, optional max_current. series_resistance/parallel_resistance: resistances (list). "
            "non_inverting_gain: rf, rg, optional vin. inverting_gain: rf, rin, optional vin. "
            "rc_time_constant: resistance, capacitance."
        )
    )


def calculate_electrical_values(ctx: ToolContext, inp: CalculateInput) -> ToolOutput:
    try:
        result = calculate(inp.calculation, inp.params)
    except CalculationError as exc:
        return ToolOutput(status="error", data={"error": str(exc)}, summary=f"invalid input: {exc}")
    outputs = ", ".join(f"{k} = {v.display}" for k, v in result.outputs.items())
    return ToolOutput(
        status="ok",
        data=result.model_dump(),
        summary=f"{result.calculation}: {outputs}",
        calculations=[result],
    )


# --------------------------------------------------------------------------- D


class RecordMeasurementInput(MeasurementCreate):
    pass


_NUM = re.compile(r"[-+]?\d*\.?\d+")


def _value_in_message(value: float, message: str) -> bool:
    for token in _NUM.findall(message):
        try:
            if abs(float(token) - value) <= 1e-9 * max(1.0, abs(value)):
                return True
        except ValueError:
            continue
    return False


def record_measurement(ctx: ToolContext, inp: RecordMeasurementInput) -> ToolOutput:
    if not _value_in_message(inp.value, ctx.user_message):
        return ToolOutput(
            status="error",
            data={"error": "The value does not appear in the user's message. Only record values the user explicitly reported; ask the user instead."},
            summary="refused: value not present in the user's message",
        )
    try:
        m = ctx.record_measurement(ctx.session, MeasurementCreate.model_validate(inp.model_dump()))
    except (UnitError, ValueError) as exc:
        return ToolOutput(status="error", data={"error": str(exc)}, summary=f"invalid measurement: {exc}")
    return ToolOutput(
        status="ok",
        data=m.model_dump(mode="json"),
        summary=f"recorded {m.quantity} {format_quantity(m.si_value, m.si_unit)} at {m.target}",
        measurements=[m],
    )


# --------------------------------------------------------------------------- E


class SimulateInput(BaseModel):
    analysis: Literal["dc_operating_point"] = "dc_operating_point"


def simulate_circuit(ctx: ToolContext, inp: SimulateInput) -> ToolOutput:
    if ctx.graph is None or ctx.graph.is_empty:
        return ToolOutput(
            status="unavailable",
            data={"error": "Simulation needs a verified netlist; this circuit has none."},
            summary="no verified netlist",
        )
    try:
        result = ctx.simulation.simulate(ctx.graph)
    except SimulationInputError as exc:
        return ToolOutput(status="error", data={"error": str(exc)}, summary=f"cannot simulate: {exc}")
    except SimulationUnavailable as exc:
        return ToolOutput(status="unavailable", data={"error": str(exc)}, summary=str(exc)[:200])
    return ToolOutput(
        status="ok",
        data={
            "node_voltages": result.node_voltages,
            "branch_currents": result.branch_currents,
            "assumptions": result.assumptions,
            "engine": "ngspice",
        },
        summary="ngspice operating point: " + ", ".join(f"V({k})={format_quantity(v, 'V')}" for k, v in result.node_voltages.items()),
        simulation_ran=True,
    )


TOOLS: list[Tool] = [
    Tool(
        name="get_component_info",
        description=(
            "Look up a component of the current circuit (type, marked value, pins, role derived from verified topology) "
            "and retrieve relevant passages from the local datasheet collection. Passages carry source file, page, section "
            "and an 'illustrative' flag. Cite passages by id."
        ),
        input_model=GetComponentInfoInput,
        handler=get_component_info,
    ),
    Tool(
        name="trace_net",
        description=(
            "Trace verified electrical connectivity: everything connected to a component, pin or net, and optionally the "
            "path between two targets. Returns status 'unavailable' with an explicit uncertainty when no verified netlist exists."
        ),
        input_model=TraceNetInput,
        handler=trace_net,
    ),
    Tool(
        name="calculate_electrical_values",
        description="Run a deterministic electrical calculation in Python. Always use this instead of doing arithmetic yourself.",
        input_model=CalculateInput,
        handler=calculate_electrical_values,
    ),
    Tool(
        name="record_measurement",
        description=(
            "Record a measurement that the user explicitly reported in their latest message (value, unit, where it was measured). "
            "Never call this with values the user did not state."
        ),
        input_model=RecordMeasurementInput,
        handler=record_measurement,
    ),
    Tool(
        name="simulate_circuit",
        description=(
            "Run an ngspice DC operating-point simulation generated from the verified netlist. Returns 'unavailable' if ngspice "
            "is not installed or no verified netlist exists; never invent simulation results."
        ),
        input_model=SimulateInput,
        handler=simulate_circuit,
    ),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}
