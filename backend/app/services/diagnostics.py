"""Deterministic circuit reasoning over *verified* connectivity.

* ``build_insight``   – classify the topology, assign component roles, compute key values.
* ``find_issues``     – potential issues, each with explicit evidence.
* ``plan_next_check`` – choose the single most useful next measurement given what
                        the user has already measured.

Nothing in this module invents values: every number is parsed from the netlist,
computed from those values, or taken from a clearly labelled assumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.chat import Finding
from app.schemas.circuit import CalculationResult, Component, DiagnosticAction, Evidence, Measurement, PotentialIssue, Uncertainty
from app.services.calculations import CalculationError, inverting_gain, led_series_resistor, non_inverting_gain, voltage_divider
from app.services.circuit_graph import CircuitGraph, is_ground
from app.services.dc_solver import (
    DEFAULT_LED_VF,
    LED_VF_SOURCE,
    DCSolution,
    SolverError,
    opamp_nets,
    resistor_value,
    solve_dc,
    source_voltage,
    two_terminal_nets,
)
from app.services.units import format_quantity

ILLUSTRATIVE_LED_MAX_CURRENT = 0.020
ILLUSTRATIVE_LED_VF_RANGE = (1.8, 2.2)
ILLUSTRATIVE_OPAMP_HEADROOM = 1.5
HIGH_VOLTAGE_THRESHOLD = 50.0

LOW_VOLTAGE_SAFETY = (
    "Low-voltage DC circuit, but still keep probes off adjacent pins to avoid accidental shorts, "
    "and remove power before any resistance measurement."
)
HIGH_VOLTAGE_SAFETY = (
    "This circuit contains a source of 50 V or more. Hazardous voltage: only measure with appropriately "
    "rated (CAT-rated) equipment, isolate power and discharge stored energy before touching the circuit, "
    "and get qualified supervision if you are unsure."
)
UNPOWERED_SAFETY = (
    "Disconnect the supply first and wait for capacitors to discharge. In-circuit resistance readings can be "
    "affected by parallel paths – lift one end of the part if the reading is suspicious."
)


@dataclass
class CircuitInsight:
    circuit_type: str
    title: str
    summary: str
    expected_behavior: str
    roles: dict[str, str]
    key: dict[str, str]
    calculations: list[CalculationResult] = field(default_factory=list)
    dc: DCSolution | None = None
    dc_error: str | None = None
    output_net: str | None = None
    uncertainties: list[Uncertainty] = field(default_factory=list)
    max_source_voltage: float | None = None

    @property
    def safety_note(self) -> str:
        if self.max_source_voltage is not None and abs(self.max_source_voltage) >= HIGH_VOLTAGE_THRESHOLD:
            return HIGH_VOLTAGE_SAFETY
        return LOW_VOLTAGE_SAFETY


# --------------------------------------------------------------------------- helpers


def _resistors_between(graph: CircuitGraph, a: str, b: str) -> list[Component]:
    out = []
    for c in graph.components.values():
        if c.type != "resistor":
            continue
        nets = {net for _, _, net in graph.component_pins(c.ref)}
        if nets == {a, b}:
            out.append(c)
    return out


def _sources(graph: CircuitGraph) -> list[Component]:
    return [c for c in graph.components.values() if c.type in ("voltage_source", "battery")]


def _safe_source_voltage(c: Component) -> float | None:
    try:
        return source_voltage(c)
    except SolverError:
        return None


def _value_text(c: Component) -> str:
    return f"{c.ref} ({c.value})" if c.value else c.ref


def generic_role(c: Component, graph: CircuitGraph) -> str:
    nets = graph.nets_of(c.ref)
    if c.type in ("voltage_source", "battery"):
        return f"DC supply{f' ({c.value})' if c.value else ''} powering the circuit"
    if c.type == "led":
        return "indicator LED (emits light when forward current flows)"
    if c.type == "capacitor":
        if any(is_ground(n) for n in nets):
            return "capacitor to ground (filtering / decoupling on its other net)"
        return "capacitor (coupling or timing)"
    if c.type == "connector":
        return "connector – interface to off-board wiring or a load"
    if c.type == "opamp":
        return "operational amplifier"
    if c.type == "resistor":
        if any(is_ground(n) for n in nets):
            return "resistor to ground"
        return "resistor"
    return c.type.replace("_", " ")


# --------------------------------------------------------------------------- classification


def build_insight(graph: CircuitGraph) -> CircuitInsight:
    roles = {ref: generic_role(c, graph) for ref, c in graph.components.items()}
    insight = CircuitInsight(
        circuit_type="general",
        title="Circuit",
        summary="",
        expected_behavior="",
        roles=roles,
        key={},
    )
    voltages = [v for v in (_safe_source_voltage(s) for s in _sources(graph)) if v is not None]
    insight.max_source_voltage = max(voltages, key=abs) if voltages else None

    try:
        insight.dc = solve_dc(graph)
    except SolverError as exc:
        insight.dc_error = str(exc)
        insight.uncertainties.append(
            Uncertainty(topic="DC operating point", detail=f"Expected node voltages unavailable: {exc}")
        )

    if not (_classify_opamp(graph, insight) or _classify_led(graph, insight) or _classify_divider(graph, insight)):
        insight.summary = (
            f"Circuit with {len(graph.components)} components and {len(graph.nets)} nets. "
            "CircuitSense does not have a dedicated template for this topology, so explanations are based on "
            "the verified connections and generic component roles."
        )
        insight.expected_behavior = "No topology-specific expectation available."
    if insight.dc:
        for a in insight.dc.assumptions:
            if "LED forward voltage" in a:
                insight.uncertainties.append(Uncertainty(topic="LED forward voltage", detail=a))
    insight.uncertainties.append(
        Uncertainty(
            topic="Tolerances and ratings",
            detail="Resistor tolerance and power ratings are not specified in the netlist; marked values are assumed exact.",
        )
    )
    return insight


def _classify_led(graph: CircuitGraph, insight: CircuitInsight) -> bool:
    for led in (c for c in graph.components.values() if c.type == "led"):
        try:
            anode, cathode = two_terminal_nets(graph, led)
        except SolverError:
            continue
        for src in _sources(graph):
            try:
                pos, neg = two_terminal_nets(graph, src)
            except SolverError:
                continue
            vs = _safe_source_voltage(src)
            series = None
            if cathode == neg:
                found = _resistors_between(graph, pos, anode)
                series = found[0] if found else None
            elif anode == pos:
                found = _resistors_between(graph, cathode, neg)
                series = found[0] if found else None
            if series is None:
                continue
            insight.circuit_type = "led_indicator"
            insight.title = "LED indicator with current-limiting resistor"
            insight.key = {"source": src.ref, "resistor": series.ref, "led": led.ref}
            insight.output_net = anode if cathode == neg else cathode
            insight.roles[series.ref] = (
                f"current-limiting resistor for {led.ref}: it drops the difference between the supply and the LED "
                "forward voltage, which sets the LED current"
            )
            insight.roles[led.ref] = f"indicator LED, driven from {src.ref} through {series.ref}"
            insight.roles[src.ref] = f"DC supply ({src.value}) for the LED branch"
            insight.summary = (
                f"{_value_text(src)} drives {_value_text(led)} through the series resistor {_value_text(series)}. "
                f"The resistor limits the LED current to a safe value."
            )
            try:
                r = resistor_value(series)
                if vs is not None:
                    calc = led_series_resistor(
                        {"supply_voltage": vs, "forward_voltage": DEFAULT_LED_VF, "resistance": r}
                    )
                    calc.assumptions.append(f"Vf = {DEFAULT_LED_VF:g} V from {LED_VF_SOURCE}.")
                    insight.calculations.append(calc)
                    i = calc.outputs["current"].display
                    insight.expected_behavior = (
                        f"With a {format_quantity(vs, 'V')} supply and an assumed {DEFAULT_LED_VF:g} V LED forward "
                        f"voltage, about {i} flows through {series.ref} and {led.ref}; the LED should be lit. "
                        f"{series.ref} drops about {calc.outputs['resistor_voltage'].display} and dissipates "
                        f"{calc.outputs['resistor_power'].display}."
                    )
            except (SolverError, CalculationError) as exc:
                insight.uncertainties.append(Uncertainty(topic="LED current", detail=str(exc)))
            return True
    return False


def _classify_divider(graph: CircuitGraph, insight: CircuitInsight) -> bool:
    gnd = graph.ground_net()
    if gnd is None:
        return False
    for src in _sources(graph):
        try:
            pos, neg = two_terminal_nets(graph, src)
        except SolverError:
            continue
        if neg != gnd:
            continue
        for mid in graph.nets:
            if mid in (pos, gnd):
                continue
            tops = _resistors_between(graph, pos, mid)
            bottoms = _resistors_between(graph, mid, gnd)
            if not (tops and bottoms):
                continue
            top, bottom = tops[0], bottoms[0]
            insight.circuit_type = "resistor_divider"
            insight.title = "Resistive voltage divider"
            insight.key = {"source": src.ref, "r_top": top.ref, "r_bottom": bottom.ref, "output_net": mid}
            insight.output_net = mid
            insight.roles[top.ref] = f"upper divider resistor between {pos} and the {mid} output node"
            insight.roles[bottom.ref] = f"lower divider resistor between the {mid} output node and {gnd}"
            insight.roles[src.ref] = f"input supply ({src.value}) for the divider"
            loads = [m.ref for m in graph.net_members(mid) if m.ref not in (top.ref, bottom.ref)]
            for ref in loads:
                if graph.components[ref].type == "connector":
                    insight.roles[ref] = f"output connector carrying {mid} (and {gnd}) to an external load"
            insight.summary = (
                f"{_value_text(top)} and {_value_text(bottom)} divide the {src.value or src.ref} input from "
                f"{_value_text(src)} down to a lower voltage on net {mid}."
            )
            vs = _safe_source_voltage(src)
            try:
                if vs is not None:
                    calc = voltage_divider(
                        {"vin": vs, "r_top": resistor_value(top), "r_bottom": resistor_value(bottom)}
                    )
                    insight.calculations.append(calc)
                    insight.expected_behavior = (
                        f"Unloaded, {mid} should sit at about {calc.outputs['vout'].display} "
                        f"({calc.outputs['ratio'].display} × {format_quantity(vs, 'V')}). Any load connected to "
                        f"{mid} appears in parallel with {bottom.ref} and pulls the output lower."
                    )
            except (SolverError, CalculationError) as exc:
                insight.uncertainties.append(Uncertainty(topic="Divider output", detail=str(exc)))
            return True
    return False


def _classify_opamp(graph: CircuitGraph, insight: CircuitInsight) -> bool:
    gnd = graph.ground_net()
    for amp in (c for c in graph.components.values() if c.type == "opamp"):
        try:
            nets = opamp_nets(graph, amp.ref)
        except SolverError:
            continue
        rfs = _resistors_between(graph, nets["out"], nets["inn"])
        if not rfs:
            continue
        rf = rfs[0]
        insight.roles[rf.ref] = (
            f"feedback resistor (Rf) from {amp.ref} output ({nets['out']}) to the inverting input ({nets['inn']})"
        )
        insight.roles[amp.ref] = "operational amplifier with negative feedback"
        rgs = _resistors_between(graph, nets["inn"], gnd) if gnd else []
        vin_value: float | None = None
        if rgs and gnd and nets["inp"] != gnd:
            rg = rgs[0]
            insight.circuit_type = "non_inverting_amplifier"
            insight.title = "Non-inverting op-amp amplifier"
            insight.key = {"opamp": amp.ref, "rf": rf.ref, "rg": rg.ref, "input_net": nets["inp"], "output_net": nets["out"], "feedback_net": nets["inn"]}
            insight.output_net = nets["out"]
            insight.roles[rg.ref] = (
                f"gain-setting resistor (Rg) from the inverting input ({nets['inn']}) to {gnd}; with "
                f"{rf.ref} it forms the feedback divider"
            )
            for src in _sources(graph):
                try:
                    pos, neg = two_terminal_nets(graph, src)
                except SolverError:
                    continue
                if pos == nets["inp"]:
                    insight.roles[src.ref] = f"input signal source ({src.value}) on {nets['inp']}"
                    vin_value = _safe_source_voltage(src)
                elif pos == gnd:
                    insight.roles[src.ref] = f"negative supply rail ({src.value}) for {neg}"
                else:
                    insight.roles[src.ref] = f"positive supply rail ({src.value}) for {pos}"
            for m in graph.net_members(nets["out"]):
                comp = graph.components[m.ref]
                if comp.type == "resistor" and m.ref != rf.ref:
                    insight.roles[m.ref] = f"load resistor on the amplifier output ({nets['out']})"
            insight.summary = (
                f"{amp.ref} amplifies the signal on {nets['inp']} without inverting it. {_value_text(rf)} and "
                f"{_value_text(rg)} feed a fraction of the output back to the inverting input, which sets the gain."
            )
            try:
                params = {"rf": resistor_value(rf), "rg": resistor_value(rg)}
                if vin_value is not None:
                    params["vin"] = vin_value
                calc = non_inverting_gain(params)
                insight.calculations.append(calc)
                g = calc.outputs["gain"].display
                vout = calc.outputs.get("vout")
                insight.expected_behavior = (
                    f"Closed-loop gain 1 + {rf.ref}/{rg.ref} = {g}. "
                    + (f"With {format_quantity(vin_value, 'V')} in, {nets['out']} should be about {vout.display}. " if vout and vin_value is not None else "")
                    + f"Negative feedback holds {nets['inn']} at the same voltage as {nets['inp']}."
                )
            except (SolverError, CalculationError) as exc:
                insight.uncertainties.append(Uncertainty(topic="Amplifier gain", detail=str(exc)))
            return True
        if gnd and nets["inp"] == gnd:
            rins = [
                c
                for c in graph.components.values()
                if c.type == "resistor" and c.ref != rf.ref and nets["inn"] in graph.nets_of(c.ref)
            ]
            if rins:
                rin = rins[0]
                insight.circuit_type = "inverting_amplifier"
                insight.title = "Inverting op-amp amplifier"
                insight.key = {"opamp": amp.ref, "rf": rf.ref, "rin": rin.ref, "output_net": nets["out"], "feedback_net": nets["inn"]}
                insight.output_net = nets["out"]
                insight.roles[rin.ref] = f"input resistor (Rin) into the virtual-ground node {nets['inn']}"
                insight.summary = f"{amp.ref} is an inverting amplifier with gain −{rf.ref}/{rin.ref}."
                try:
                    calc = inverting_gain({"rf": resistor_value(rf), "rin": resistor_value(rin)})
                    insight.calculations.append(calc)
                    insight.expected_behavior = f"Closed-loop gain {calc.outputs['gain'].display}."
                except (SolverError, CalculationError) as exc:
                    insight.uncertainties.append(Uncertainty(topic="Amplifier gain", detail=str(exc)))
                return True
    return False


# --------------------------------------------------------------------------- issues


def find_issues(graph: CircuitGraph, insight: CircuitInsight) -> list[PotentialIssue]:
    issues: list[PotentialIssue] = []
    for net in graph.nets.values():
        if len(net.nodes) == 1:
            node = net.nodes[0]
            issues.append(
                PotentialIssue(
                    id=f"dangling-{net.name}",
                    title=f"Net {net.name} has a single connection",
                    severity="warning",
                    status="confirmed",
                    description=f"Only {node.ref}.{node.pin} connects to {net.name}; the pin may be unconnected by mistake.",
                    evidence=[Evidence(kind="parsed_netlist", detail=f"net {net.name}: 1 node ({node.ref}.{node.pin})")],
                    related_components=[node.ref],
                )
            )

    for led in (c for c in graph.components.values() if c.type == "led"):
        has_resistor = any(graph.components[n].type == "resistor" for n in graph.neighbors_of(led.ref))
        if not has_resistor:
            issues.append(
                PotentialIssue(
                    id=f"led-no-resistor-{led.ref}",
                    title=f"{led.ref} has no resistor on either terminal",
                    severity="critical",
                    status="confirmed",
                    description=(
                        f"No resistor shares a net with {led.ref}. Unless the driver limits current, the LED current "
                        "is set only by the supply's internal resistance and can destroy the LED."
                    ),
                    evidence=[Evidence(kind="parsed_netlist", detail=f"neighbours of {led.ref}: {', '.join(sorted(graph.neighbors_of(led.ref))) or 'none'}")],
                    related_components=[led.ref],
                )
            )

    if insight.circuit_type == "led_indicator":
        for calc in insight.calculations:
            if calc.calculation == "led_series_resistor":
                i = calc.outputs["current"].value
                if i > ILLUSTRATIVE_LED_MAX_CURRENT:
                    issues.append(
                        PotentialIssue(
                            id="led-overcurrent",
                            title="Estimated LED current above a typical 20 mA indicator rating",
                            severity="warning",
                            status="hypothesis",
                            description=(
                                f"The estimated current {calc.outputs['current'].display} exceeds 20 mA, the maximum "
                                "continuous current in the illustrative generic LED reference. Check your LED's datasheet."
                            ),
                            evidence=[
                                Evidence(kind="calculation", detail=calc.steps[1] if len(calc.steps) > 1 else calc.formula),
                                Evidence(kind="datasheet", detail="max continuous forward current 20 mA (illustrative)", source="generic_red_led.md"),
                            ],
                            related_components=[insight.key.get("led", ""), insight.key.get("resistor", "")],
                        )
                    )

    if insight.circuit_type in ("non_inverting_amplifier", "inverting_amplifier") and insight.dc:
        amp = graph.components[insight.key["opamp"]]
        rails = []
        for pname in ("V+", "V-"):
            net = graph.pin_net(amp.ref, pname)
            if net is not None:
                rails.append(insight.dc.node_voltages.get(net, 0.0))
        out_net = insight.key["output_net"]
        vout = insight.dc.node_voltages.get(out_net)
        if len(rails) == 2 and vout is not None:
            hi, lo = max(rails), min(rails)
            if vout > hi - ILLUSTRATIVE_OPAMP_HEADROOM or vout < lo + ILLUSTRATIVE_OPAMP_HEADROOM:
                issues.append(
                    PotentialIssue(
                        id="opamp-clipping",
                        title=f"{amp.ref} output may clip against the supply rails",
                        severity="warning",
                        status="hypothesis",
                        description=(
                            f"The ideal output ({format_quantity(vout, 'V')}) is within {ILLUSTRATIVE_OPAMP_HEADROOM} V of a rail "
                            f"({format_quantity(lo, 'V')} / {format_quantity(hi, 'V')}). Many non rail-to-rail op-amps cannot swing that far."
                        ),
                        evidence=[
                            Evidence(kind="calculation", detail=f"ideal V({out_net}) = {format_quantity(vout, 'V')}"),
                            Evidence(kind="datasheet", detail="~1.5 V output headroom (illustrative generic op-amp)", source="generic_opamp.md"),
                        ],
                        related_components=[amp.ref],
                    )
                )
    for comp in graph.components.values():
        if comp.type in ("resistor", "voltage_source", "battery") and not comp.value:
            issues.append(
                PotentialIssue(
                    id=f"missing-value-{comp.ref}",
                    title=f"{comp.ref} has no value in the netlist",
                    severity="info",
                    status="confirmed",
                    description=f"Calculations involving {comp.ref} are not possible until its value is known.",
                    evidence=[Evidence(kind="parsed_netlist", detail=f"{comp.ref} value field is empty")],
                    related_components=[comp.ref],
                )
            )
    return issues


# --------------------------------------------------------------------------- measurement planning


@dataclass
class Check:
    id: str
    quantity: str
    target: str  # net name (voltage vs ground) or component ref (across / resistance)
    nominal: float | None
    tolerance: float | None
    unit: str
    action: str
    reason: str
    power_state: str = "powered"
    expected_text: str | None = None
    related: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


@dataclass
class CheckEvaluation:
    check: Check
    measurement: Measurement
    deviates: bool | None
    detail: str


@dataclass
class PlanResult:
    next_action: DiagnosticAction | None
    evaluations: list[CheckEvaluation]
    confirmed: list[Finding]
    hypotheses: list[Finding]


def _tol(nominal: float, rel: float = 0.05, floor: float = 0.1) -> float:
    return max(abs(nominal) * rel, floor)


def _node_v(insight: CircuitInsight, net: str) -> float | None:
    return insight.dc.node_voltages.get(net) if insight.dc else None


def build_checks(graph: CircuitGraph, insight: CircuitInsight) -> list[Check]:
    checks: list[Check] = []
    gnd = graph.ground_net() or "GND"
    key = insight.key

    def voltage_check(cid: str, net: str, why: str, related: list[str], rel: float = 0.05) -> Check:
        nominal = _node_v(insight, net)
        return Check(
            id=cid,
            quantity="voltage",
            target=net,
            nominal=nominal,
            tolerance=_tol(nominal, rel) if nominal is not None else None,
            unit="V",
            action=f"Measure the DC voltage on net {net} with respect to {gnd} (circuit powered).",
            reason=why,
            expected_text=f"≈ {format_quantity(nominal, 'V')}" if nominal is not None else None,
            related=related,
        )

    def resistance_check(ref: str, why: str) -> Check:
        comp = graph.components[ref]
        try:
            nominal = resistor_value(comp)
        except SolverError:
            nominal = None
        return Check(
            id=f"res-{ref}",
            quantity="resistance",
            target=ref,
            nominal=nominal,
            tolerance=nominal * 0.05 if nominal else None,
            unit="Ω",
            action=f"With power removed, measure the resistance of {ref} (marked {comp.value}).",
            reason=why,
            power_state="unpowered",
            expected_text=f"{comp.value} ±5%" if comp.value else None,
            related=[ref],
        )

    sources = _sources(graph)
    for src in sources:
        try:
            pos, neg = two_terminal_nets(graph, src)
        except SolverError:
            continue
        net = pos if is_ground(neg) else neg
        supply_check = voltage_check(
            f"supply-{src.ref}",
            net,
            f"Confirms that {src.ref} is actually delivering its rated voltage under load. If the supply is wrong, "
            "every downstream reading is wrong too, so this rules out the cheapest explanation first.",
            [src.ref],
        )
        supply_check.aliases.append(src.ref)
        checks.append(supply_check)
    # Order supply checks: input sources after power rails for amplifiers.
    if insight.circuit_type == "led_indicator":
        led, res = key["led"], key["resistor"]
        checks.append(
            Check(
                id=f"vf-{led}",
                quantity="voltage",
                target=led,
                nominal=DEFAULT_LED_VF,
                tolerance=(ILLUSTRATIVE_LED_VF_RANGE[1] - ILLUSTRATIVE_LED_VF_RANGE[0]) / 2,
                unit="V",
                action=f"Measure the voltage across {led} (red probe on the anode, black on the cathode).",
                reason=(
                    "The LED forward voltage tells you whether the LED is conducting normally (≈1.8–2.2 V for a typical "
                    "red LED), shorted (≈0 V) or open (close to the full supply)."
                ),
                expected_text="≈ 1.8–2.2 V for a typical red LED (illustrative)",
                related=[led],
            )
        )
        res_v = None
        for calc in insight.calculations:
            if calc.calculation == "led_series_resistor":
                res_v = calc.outputs["resistor_voltage"].value
        checks.append(
            Check(
                id=f"vr-{res}",
                quantity="voltage",
                target=res,
                nominal=res_v,
                tolerance=_tol(res_v, 0.15, 0.2) if res_v is not None else None,
                unit="V",
                action=f"Measure the voltage across {res}.",
                reason=f"Dividing this voltage by {res}'s resistance gives the actual LED current (Ohm's law).",
                expected_text=f"≈ {format_quantity(res_v, 'V')}" if res_v is not None else None,
                related=[res],
            )
        )
        checks.append(resistance_check(res, f"Confirms {res} is the marked value and not open or damaged."))
    elif insight.circuit_type == "resistor_divider":
        out = key["output_net"]
        checks.append(
            voltage_check(
                "divider-output",
                out,
                f"This is the symptom node. Comparing it with the expected divider output shows whether the problem "
                f"is in the divider itself or elsewhere.",
                [key["r_top"], key["r_bottom"]],
            )
        )
        checks.append(resistance_check(key["r_bottom"], f"A low {key['r_bottom']} (or a load in parallel) pulls the output down."))
        checks.append(resistance_check(key["r_top"], f"A high or open {key['r_top']} pulls the output down."))
    elif insight.circuit_type in ("non_inverting_amplifier", "inverting_amplifier"):
        amp = key["opamp"]
        nets = opamp_nets(graph, amp)
        # Power rails first, then input, output and feedback node.
        rails = [c for c in checks if c.id.startswith("supply-") and c.target not in (nets["inp"],)]
        inputs = [c for c in checks if c.id.startswith("supply-") and c.target == nets["inp"]]
        checks = rails + inputs
        checks.append(
            voltage_check(
                "amp-output",
                nets["out"],
                "The output is the symptom node; compare it with the gain calculation.",
                [amp],
            )
        )
        checks.append(
            voltage_check(
                "amp-feedback",
                nets["inn"],
                f"With working negative feedback, {nets['inn']} sits at the same voltage as {nets['inp']} "
                "(virtual short). A large difference means the feedback path is broken or the op-amp is saturated.",
                [key["rf"], amp],
                rel=0.1,
            )
        )
        checks.append(resistance_check(key["rf"], f"An open or wrong-value {key['rf']} changes the gain directly."))
        other = key.get("rg") or key.get("rin")
        if other:
            checks.append(resistance_check(other, f"{other} sets the gain together with {key['rf']}."))
    elif insight.output_net:
        checks.append(voltage_check("output", insight.output_net, "Symptom node.", []))
    return checks


def _resolve_measurement_target(graph: CircuitGraph, m: Measurement) -> str:
    resolved = graph.resolve(m.target)
    if resolved.kind == "pin":
        return graph.g.nodes[resolved.node]["net"]
    if resolved.kind in ("net", "component"):
        return resolved.label
    return m.target.upper()


def evaluate_measurements(graph: CircuitGraph, checks: list[Check], measurements: list[Measurement]) -> list[CheckEvaluation]:
    evaluations: list[CheckEvaluation] = []
    for check in checks:
        for m in reversed(measurements):  # latest measurement wins
            if m.quantity != check.quantity:
                continue
            target = _resolve_measurement_target(graph, m)
            if target != check.target and target not in check.aliases:
                continue
            if check.quantity == "voltage" and check.target in graph.nets and m.reference and not is_ground(m.reference):
                continue
            if check.nominal is None or check.tolerance is None:
                evaluations.append(CheckEvaluation(check, m, None, "no expected value to compare against"))
            else:
                diff = m.si_value - check.nominal
                deviates = abs(diff) > check.tolerance
                detail = (
                    f"measured {format_quantity(m.si_value, check.unit)} vs expected "
                    f"{format_quantity(check.nominal, check.unit)} ± {format_quantity(check.tolerance, check.unit)}"
                )
                evaluations.append(CheckEvaluation(check, m, deviates, detail))
            break
    return evaluations


def plan_next_check(graph: CircuitGraph, insight: CircuitInsight, measurements: list[Measurement]) -> PlanResult:
    checks = build_checks(graph, insight)
    evaluations = evaluate_measurements(graph, checks, measurements)
    by_id = {e.check.id: e for e in evaluations}
    confirmed: list[Finding] = []
    hypotheses: list[Finding] = []
    for e in evaluations:
        if e.deviates is None:
            continue
        verdict = "outside" if e.deviates else "within"
        confirmed.append(
            Finding(
                statement=f"{e.check.target} {e.check.quantity}: {e.detail} — {verdict} the expected range.",
                basis="user_measurement",
                detail=f"recorded {e.measurement.recorded_at.isoformat(timespec='seconds')}",
            )
        )

    def action(check: Check, prefix: str = "") -> DiagnosticAction:
        return DiagnosticAction(
            action=prefix + check.action,
            reason=check.reason,
            target=check.target,
            expected=check.expected_text,
            safety_note=UNPOWERED_SAFETY if check.power_state == "unpowered" else insight.safety_note,
        )

    # A deviating supply explains everything downstream: stop there.
    for e in evaluations:
        if e.check.id.startswith("supply-") and e.deviates:
            src = e.check.related[0]
            hypotheses.append(
                Finding(
                    statement=f"The problem is upstream: {src} is not delivering its rated voltage "
                    "(depleted battery, current-limited bench supply, bad wiring, or an overload/short downstream).",
                    basis="user_measurement",
                )
            )
            return PlanResult(
                next_action=DiagnosticAction(
                    action=f"Disconnect the circuit from {src} and measure {src}'s open-circuit voltage.",
                    reason=(
                        "If the open-circuit voltage is correct, the circuit is loading the supply too heavily "
                        "(look for a short); if it is still wrong, the supply itself is the fault."
                    ),
                    target=src,
                    expected=e.check.expected_text,
                    safety_note=insight.safety_note,
                ),
                evaluations=evaluations,
                confirmed=confirmed,
                hypotheses=hypotheses,
            )

    hypotheses.extend(_symptom_hypotheses(insight, evaluations))
    for check in checks:
        if check.id not in by_id:
            return PlanResult(action(check), evaluations, confirmed, hypotheses)

    deviating = [e for e in evaluations if e.deviates]
    if deviating:
        e = deviating[-1]
        return PlanResult(
            next_action=DiagnosticAction(
                action=f"Inspect {', '.join(e.check.related) or e.check.target} and its solder joints, and compare the part markings with the schematic.",
                reason="All planned measurements are recorded and at least one is outside its expected range; a wrong, damaged or misplaced part is the most likely cause.",
                target=e.check.target,
                safety_note=UNPOWERED_SAFETY,
            ),
            evaluations=evaluations,
            confirmed=confirmed,
            hypotheses=hypotheses,
        )
    return PlanResult(
        next_action=DiagnosticAction(
            action="Describe the exact symptom (what you expected vs. what you observe) and under which conditions it appears.",
            reason="Every recorded measurement matches the expected DC operating point, so the fault may be intermittent, load-dependent or outside this schematic.",
        ),
        evaluations=evaluations,
        confirmed=confirmed,
        hypotheses=hypotheses,
    )


def _symptom_hypotheses(insight: CircuitInsight, evaluations: list[CheckEvaluation]) -> list[Finding]:
    """Ranked hypotheses for the circuit type (stated as hypotheses, never as findings)."""
    k = insight.key
    if insight.circuit_type == "resistor_divider":
        out_eval = next((e for e in evaluations if e.check.id == "divider-output"), None)
        if out_eval and out_eval.deviates is False:
            return []
        return [
            Finding(statement=f"A load connected to {k['output_net']} sits in parallel with {k['r_bottom']} and pulls the output down.", basis="calculation"),
            Finding(statement=f"{k['r_bottom']} is lower than marked, or {k['r_top']} is higher than marked (wrong part or damage).", basis="calculation"),
            Finding(statement=f"The input from {k['source']} is lower than expected.", basis="calculation"),
        ]
    if insight.circuit_type == "led_indicator":
        return [
            Finding(statement=f"{k['led']} is reversed or open (no current flows).", basis="calculation"),
            Finding(statement=f"{k['resistor']} is a higher value than marked or open, starving the LED of current.", basis="calculation"),
            Finding(statement=f"The supply {k['source']} is low or disconnected.", basis="calculation"),
        ]
    if insight.circuit_type in ("non_inverting_amplifier", "inverting_amplifier"):
        return [
            Finding(statement=f"One of the op-amp supply rails is missing, so {k['opamp']} cannot drive its output.", basis="calculation"),
            Finding(statement=f"The feedback path through {k['rf']} is open or the wrong value, changing the gain.", basis="calculation"),
            Finding(statement="The output is loaded too heavily or is clipping against a rail.", basis="calculation"),
        ]
    return []
