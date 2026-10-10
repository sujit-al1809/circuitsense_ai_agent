"""Optional ngspice DC operating-point simulation.

Safety model:
* Users can never supply SPICE text. The deck is generated here from a *verified*
  (parsed netlist) circuit graph, using only supported element types.
* Every value is parsed to a float and re-formatted; designators and net names are
  sanitised to ``[A-Za-z0-9_]``. Nothing user-controlled reaches the deck verbatim.
* ngspice runs without a shell, in a temporary directory, in batch mode, with a timeout.
* If ngspice is not installed, the service reports that clearly – it never fakes results.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.services.circuit_graph import CircuitGraph, is_ground
from app.services.dc_solver import (
    DEFAULT_DIODE_VF,
    SolverError,
    opamp_nets,
    resistor_value,
    source_voltage,
    two_terminal_nets,
)

SUPPORTED_TYPES = {"resistor", "voltage_source", "battery", "led", "diode", "opamp", "capacitor"}
MAX_ELEMENTS = 200
_SAFE = re.compile(r"[^A-Za-z0-9_]")

# Generic models, stated as assumptions in every result.
LED_MODEL = ".model DLED D(IS=1e-20 N=1.8 RS=2)"
DIODE_MODEL = ".model DGEN D(IS=1e-14 N=1 RS=0.5)"


class SimulationUnavailable(RuntimeError):
    pass


class SimulationInputError(ValueError):
    pass


@dataclass
class SimulationResult:
    node_voltages: dict[str, float]
    branch_currents: dict[str, float]
    assumptions: list[str]
    deck: str
    raw_output_tail: str = ""
    warnings: list[str] = field(default_factory=list)


def _safe(name: str) -> str:
    cleaned = _SAFE.sub("_", name)[:32]
    if not cleaned:
        raise SimulationInputError(f"cannot sanitise name {name!r}")
    return cleaned


def build_spice_deck(graph: CircuitGraph) -> tuple[str, list[str], dict[str, str]]:
    """Return (deck, assumptions, spice-node -> original-net map)."""
    if graph.is_empty:
        raise SimulationInputError("No verified netlist is available; simulation requires parsed connectivity.")
    gnd = graph.ground_net()
    if gnd is None:
        raise SimulationInputError("The netlist has no ground net (GND/0); SPICE needs a reference node.")
    unsupported = sorted(
        f"{c.ref} ({c.type})" for c in graph.components.values() if c.type not in SUPPORTED_TYPES and graph.component_pins(c.ref)
    )
    if unsupported:
        raise SimulationInputError(f"Unsupported component types for simulation: {', '.join(unsupported)}.")
    if len(graph.components) > MAX_ELEMENTS:
        raise SimulationInputError(f"Too many components to simulate (max {MAX_ELEMENTS}).")

    node_map: dict[str, str] = {}

    def node(net: str) -> str:
        if is_ground(net):
            return "0"
        spice = "n_" + _safe(net).lower()
        node_map[spice] = net
        return spice

    lines = ["* CircuitSense generated deck (DC operating point)"]
    assumptions = ["Deck generated from the verified netlist only.", "DC operating point (.op)."]
    models: set[str] = set()
    for comp in sorted(graph.components.values(), key=lambda c: c.ref):
        if not graph.component_pins(comp.ref):
            continue
        ref = _safe(comp.ref)
        try:
            if comp.type == "resistor":
                a, b = two_terminal_nets(graph, comp)
                lines.append(f"R{ref} {node(a)} {node(b)} {resistor_value(comp):.6g}")
            elif comp.type == "capacitor":
                a, b = two_terminal_nets(graph, comp)
                lines.append(f"C{ref} {node(a)} {node(b)} 1e-9")
                assumptions.append(f"{comp.ref}: capacitance irrelevant at DC (placeholder 1 nF).")
            elif comp.type in ("voltage_source", "battery"):
                p, n = two_terminal_nets(graph, comp)
                lines.append(f"V{ref} {node(p)} {node(n)} DC {source_voltage(comp):.6g}")
            elif comp.type in ("led", "diode"):
                a, k = two_terminal_nets(graph, comp)
                model = "DLED" if comp.type == "led" else "DGEN"
                models.add(LED_MODEL if comp.type == "led" else DIODE_MODEL)
                lines.append(f"D{ref} {node(a)} {node(k)} {model}")
            elif comp.type == "opamp":
                nets = opamp_nets(graph, comp.ref)
                lines.append(f"E{ref} {node(nets['out'])} 0 {node(nets['inp'])} {node(nets['inn'])} 1e5")
        except SolverError as exc:
            raise SimulationInputError(str(exc)) from exc
    if any(c.type == "led" for c in graph.components.values()):
        assumptions.append("LEDs use a generic illustrative diode model (≈1.8–2 V at mA currents), not a vendor model.")
    if any(c.type == "diode" for c in graph.components.values()):
        assumptions.append(f"Diodes use a generic silicon model (≈{DEFAULT_DIODE_VF} V forward).")
    if any(c.type == "opamp" for c in graph.components.values()):
        assumptions.append("Op-amps are ideal VCVS (gain 1e5) without rail limits or supply current.")
    lines.extend(sorted(models))
    lines += [".control", "op", "print all", "print all#branch", ".endc", ".end"]
    return "\n".join(lines) + "\n", assumptions, node_map


_VALUE_LINE = re.compile(r"^\s*([A-Za-z0-9_#().]+)\s*=\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*$")


def parse_ngspice_output(text: str, node_map: dict[str, str]) -> tuple[dict[str, float], dict[str, float]]:
    voltages: dict[str, float] = {}
    currents: dict[str, float] = {}
    for line in text.splitlines():
        m = _VALUE_LINE.match(line)
        if not m:
            continue
        name, value = m.group(1).lower(), float(m.group(2))
        if name.endswith("#branch"):
            currents[name[: -len("#branch")].upper()] = value
            continue
        if name.startswith("v(") and name.endswith(")"):
            name = name[2:-1]
        if name in node_map:
            voltages[node_map[name]] = value
    return voltages, currents


class SimulationService:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def executable(self) -> str | None:
        candidate = self.settings.ngspice_path or "ngspice"
        return shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)

    @property
    def available(self) -> bool:
        return self.executable is not None

    def simulate(self, graph: CircuitGraph) -> SimulationResult:
        deck, assumptions, node_map = build_spice_deck(graph)
        exe = self.executable
        if exe is None:
            raise SimulationUnavailable(
                "ngspice is not installed on the server (or NGSPICE_PATH is not set), so no simulation was run."
            )
        with tempfile.TemporaryDirectory(prefix="circuitsense-sim-") as tmp:
            deck_path = Path(tmp) / "circuit.cir"
            deck_path.write_text(deck, encoding="ascii")
            try:
                proc = subprocess.run(
                    [exe, "-b", str(deck_path)],
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                    timeout=self.settings.simulation_timeout_s,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired as exc:
                raise SimulationUnavailable(f"ngspice timed out after {self.settings.simulation_timeout_s:g} s.") from exc
            except OSError as exc:
                raise SimulationUnavailable(f"ngspice could not be started: {exc}") from exc
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        voltages, currents = parse_ngspice_output(output, node_map)
        if proc.returncode != 0 or not voltages:
            raise SimulationUnavailable(
                f"ngspice did not produce an operating point (exit code {proc.returncode}). "
                f"Output tail: {output.strip()[-400:]}"
            )
        gnd = graph.ground_net()
        if gnd:
            voltages[gnd] = 0.0
        return SimulationResult(
            node_voltages=voltages,
            branch_currents=currents,
            assumptions=assumptions,
            deck=deck,
            raw_output_tail=output.strip()[-1500:],
        )
