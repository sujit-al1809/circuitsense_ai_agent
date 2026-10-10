"""Idealised DC operating-point estimate (modified nodal analysis, pure Python).

This is a *calculation*, not a circuit simulation: it uses deliberately simple
models so expected node voltages can be compared with user measurements.

Models / assumptions (always reported back to the caller):
* resistors / potentiometers (2-pin) – ideal at their marked value
* DC voltage sources and batteries – ideal, value parsed from the component value
* LEDs and diodes – constant forward-voltage drop when forward biased, open otherwise
* op-amps – ideal voltage-controlled voltage source (gain 1e6), **no output rail limits**
* capacitors – open circuit; inductors / fuses – short circuit; everything else – ignored (open)

Only verified (netlist) connectivity is ever passed in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.schemas.circuit import Component
from app.services.circuit_graph import CircuitGraph, is_ground
from app.services.units import UnitError, parse_quantity

DEFAULT_LED_VF = 2.0
DEFAULT_DIODE_VF = 0.7
OPAMP_GAIN = 1e6
GMIN = 1e-12

LED_VF_SOURCE = "generic_red_led.md (illustrative reference, typical Vf)"


class SolverError(ValueError):
    pass


@dataclass
class DCSolution:
    node_voltages: dict[str, float]
    source_currents: dict[str, float]
    component_currents: dict[str, float]
    assumptions: list[str]
    ignored_components: list[str] = field(default_factory=list)
    diode_states: dict[str, bool] = field(default_factory=dict)


def source_voltage(comp: Component) -> float:
    if not comp.value:
        raise SolverError(f"{comp.ref} has no marked voltage")
    text = re.sub(r"(?i)\b(dc|vdc)\b", " ", comp.value).strip()
    text = text.replace(" ", "")
    try:
        return parse_quantity(text, expected_unit="V")
    except UnitError as exc:
        raise SolverError(f"cannot read the voltage of {comp.ref} from value {comp.value!r}") from exc


def resistor_value(comp: Component) -> float:
    if not comp.value:
        raise SolverError(f"{comp.ref} has no marked resistance")
    try:
        r = parse_quantity(comp.value.replace(" ", ""), expected_unit="Ω")
    except UnitError as exc:
        raise SolverError(f"cannot read the resistance of {comp.ref} from value {comp.value!r}") from exc
    if r <= 0:
        raise SolverError(f"{comp.ref} has a non-positive resistance")
    return r


def _pin_by_name(graph: CircuitGraph, ref: str, names: tuple[str, ...], fallback_pin: str | None) -> str:
    pins = graph.component_pins(ref)
    for pin, pname, net in pins:
        if pname and pname.upper() in names:
            return net
    if fallback_pin is not None:
        for pin, _, net in pins:
            if pin == fallback_pin:
                return net
    raise SolverError(f"cannot identify the {'/'.join(names)} pin of {ref}")


def two_terminal_nets(graph: CircuitGraph, comp: Component) -> tuple[str, str]:
    """(positive/anode, negative/cathode) nets for polarised parts; pin order otherwise."""
    if comp.type in ("led", "diode", "zener"):
        return (
            _pin_by_name(graph, comp.ref, ("A", "ANODE", "+"), "2"),
            _pin_by_name(graph, comp.ref, ("K", "CATHODE", "-"), "1"),
        )
    if comp.type in ("voltage_source", "battery"):
        return (
            _pin_by_name(graph, comp.ref, ("+", "V+", "POS"), "1"),
            _pin_by_name(graph, comp.ref, ("-", "V-", "NEG"), "2"),
        )
    pins = graph.component_pins(comp.ref)
    if len(pins) != 2:
        raise SolverError(f"{comp.ref} is expected to have 2 connected pins, found {len(pins)}")
    return pins[0][2], pins[1][2]


def opamp_nets(graph: CircuitGraph, ref: str) -> dict[str, str]:
    names = {"inp": ("+", "IN+", "+IN", "NONINV"), "inn": ("-", "IN-", "-IN", "INV"), "out": ("OUT", "~", "VOUT")}
    fallback = {"inp": "3", "inn": "2", "out": "6"}
    return {k: _pin_by_name(graph, ref, v, fallback[k]) for k, v in names.items()}


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-18:
            raise SolverError("circuit matrix is singular (floating node or source loop)")
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


def solve_dc(graph: CircuitGraph, led_vf: float = DEFAULT_LED_VF, diode_vf: float = DEFAULT_DIODE_VF) -> DCSolution:
    if graph.is_empty:
        raise SolverError("no verified connectivity")
    gnd = graph.ground_net()
    if gnd is None:
        raise SolverError("no ground net (GND/0) in the netlist")

    nodes = [n for n in graph.nets if not is_ground(n)]
    idx = {n: i for i, n in enumerate(nodes)}

    resistors: list[tuple[str, str, str, float]] = []
    vsources: list[tuple[str, str, str, float]] = []  # name, pos, neg, volts
    diodes: list[tuple[str, str, str, float]] = []  # ref, anode, cathode, vf
    opamps: list[tuple[str, dict[str, str]]] = []
    shorts: list[tuple[str, str, str]] = []
    ignored: list[str] = []
    assumptions = [
        "Idealised DC estimate (calculation, not an ngspice simulation).",
        "Capacitors are open circuits; inductors and fuses are shorts at DC.",
    ]

    for comp in graph.components.values():
        if not graph.component_pins(comp.ref):
            continue
        t = comp.type
        if t in ("resistor",):
            a, b = two_terminal_nets(graph, comp)
            resistors.append((comp.ref, a, b, resistor_value(comp)))
        elif t in ("voltage_source", "battery"):
            p, n = two_terminal_nets(graph, comp)
            vsources.append((comp.ref, p, n, source_voltage(comp)))
        elif t in ("led", "diode", "zener"):
            a, k = two_terminal_nets(graph, comp)
            vf = led_vf if t == "led" else diode_vf
            diodes.append((comp.ref, a, k, vf))
        elif t == "opamp":
            opamps.append((comp.ref, opamp_nets(graph, comp.ref)))
        elif t in ("inductor", "fuse"):
            a, b = two_terminal_nets(graph, comp)
            shorts.append((comp.ref, a, b))
        elif t == "capacitor":
            continue
        else:
            ignored.append(comp.ref)

    if any(d for d in diodes if graph.components[d[0]].type == "led"):
        assumptions.append(f"LED forward voltage assumed {led_vf:g} V ({LED_VF_SOURCE}); measure the real Vf.")
    if any(d for d in diodes if graph.components[d[0]].type != "led"):
        assumptions.append(f"Silicon diode forward voltage assumed {diode_vf:g} V.")
    if opamps:
        assumptions.append("Op-amps modelled as ideal amplifiers without output-swing (rail) limits.")
    if ignored:
        assumptions.append(f"Not modelled (treated as open): {', '.join(sorted(ignored))}.")

    states = {d[0]: True for d in diodes}
    solution: list[float] = []
    for _ in range(12):
        active = [d for d in diodes if states[d[0]]]
        branches = (
            [(s[0], s[1], s[2], s[3]) for s in vsources]
            + [(d[0], d[1], d[2], d[3]) for d in active]
            + [(s[0], s[1], s[2], 0.0) for s in shorts]
        )
        size = len(nodes) + len(branches) + len(opamps)
        a = [[0.0] * size for _ in range(size)]
        b = [0.0] * size

        def stamp_g(n1: str, n2: str, g: float) -> None:
            i1, i2 = idx.get(n1), idx.get(n2)
            if i1 is not None:
                a[i1][i1] += g
            if i2 is not None:
                a[i2][i2] += g
            if i1 is not None and i2 is not None:
                a[i1][i2] -= g
                a[i2][i1] -= g

        for i in range(len(nodes)):
            a[i][i] += GMIN
        for _, n1, n2, r in resistors:
            stamp_g(n1, n2, 1.0 / r)
        for k, (_, p, n, v) in enumerate(branches):
            row = len(nodes) + k
            ip, i_n = idx.get(p), idx.get(n)
            if ip is not None:
                a[ip][row] += 1
                a[row][ip] += 1
            if i_n is not None:
                a[i_n][row] -= 1
                a[row][i_n] -= 1
            b[row] = v
        for k, (_, nets) in enumerate(opamps):
            row = len(nodes) + len(branches) + k
            io, ip, i_n = idx.get(nets["out"]), idx.get(nets["inp"]), idx.get(nets["inn"])
            if io is not None:
                a[io][row] += 1
                a[row][io] += 1
            if ip is not None:
                a[row][ip] -= OPAMP_GAIN
            if i_n is not None:
                a[row][i_n] += OPAMP_GAIN

        solution = _solve_linear(a, b)
        v = {n: solution[i] for n, i in idx.items()}
        v[gnd] = 0.0
        for other in graph.nets:
            if is_ground(other):
                v[other] = 0.0

        changed = False
        branch_current = {br[0]: solution[len(nodes) + k] for k, br in enumerate(branches)}
        for ref, an, ka, vf in diodes:
            if states[ref] and branch_current[ref] < -1e-12:
                states[ref] = False
                changed = True
            elif not states[ref] and v.get(an, 0) - v.get(ka, 0) > vf + 1e-9:
                states[ref] = True
                changed = True
        if not changed:
            break
    else:
        raise SolverError("diode operating states did not converge")

    comp_currents: dict[str, float] = {}
    for ref, n1, n2, r in resistors:
        comp_currents[ref] = (v.get(n1, 0) - v.get(n2, 0)) / r
    source_currents: dict[str, float] = {}
    for ref, *_ in vsources:
        # MNA branch current flows into the + terminal; report current delivered by the source.
        source_currents[ref] = -branch_current[ref]
    for ref, *_ in diodes:
        comp_currents[ref] = branch_current.get(ref, 0.0) if states[ref] else 0.0

    return DCSolution(
        node_voltages=v,
        source_currents=source_currents,
        component_currents=comp_currents,
        assumptions=assumptions,
        ignored_components=sorted(ignored),
        diode_states=states,
    )
