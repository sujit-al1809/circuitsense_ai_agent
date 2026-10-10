"""Graph model of circuit connectivity.

The graph is tripartite: ``comp:R1`` — ``pin:R1.1`` — ``net:VCC``. Component
nodes connect to their own pins; pins connect to the net they sit on. Only
*verified* nets (parsed from a netlist) are loaded into ``CircuitGraph``; image
inferred connectivity is kept separately and only ever surfaced as a hint.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from app.schemas.circuit import CircuitAnalysis, Component, Net, TracedConnection, TracedNet, TraceResult

GROUND_NAMES = {"GND", "0", "GNDA", "GNDD", "AGND", "DGND", "VSS", "COM", "GROUND"}


def comp_node(ref: str) -> str:
    return f"comp:{ref}"


def pin_node(ref: str, pin: str) -> str:
    return f"pin:{ref}.{pin}"


def net_node(name: str) -> str:
    return f"net:{name}"


def is_ground(net: str) -> bool:
    return net.upper().lstrip("/") in GROUND_NAMES


@dataclass
class ResolvedTarget:
    kind: str  # component | pin | net | unknown
    node: str | None
    label: str


class CircuitGraph:
    def __init__(self, components: list[Component], nets: list[Net]):
        self.components = {c.ref: c for c in components}
        self.nets = {n.name: n for n in nets}
        self.g = nx.Graph()
        for c in components:
            self.g.add_node(comp_node(c.ref), kind="component", ref=c.ref)
        for n in nets:
            self.g.add_node(net_node(n.name), kind="net", name=n.name)
            for node in n.nodes:
                pn = pin_node(node.ref, node.pin)
                self.g.add_node(pn, kind="pin", ref=node.ref, pin=node.pin, pin_name=node.pin_name, net=n.name)
                self.g.add_edge(comp_node(node.ref), pn)
                self.g.add_edge(pn, net_node(n.name))

    # ------------------------------------------------------------------ factories
    @classmethod
    def from_analysis(cls, analysis: CircuitAnalysis, *, verification: str = "verified") -> "CircuitGraph":
        nets = [n for n in analysis.nets if n.verification == verification]
        return cls(analysis.components, nets)

    @property
    def is_empty(self) -> bool:
        return not self.nets

    # ------------------------------------------------------------------ queries
    def component_pins(self, ref: str) -> list[tuple[str, str | None, str]]:
        """Return (pin, pin_name, net) for each connected pin of a component."""
        out = []
        for nb in self.g.neighbors(comp_node(ref)):
            data = self.g.nodes[nb]
            if data.get("kind") == "pin":
                out.append((data["pin"], data.get("pin_name"), data["net"]))
        return sorted(out, key=lambda t: (int(t[0]) if t[0].isdigit() else 10_000, t[0]))

    def net_members(self, net: str) -> list[TracedConnection]:
        members = []
        for nb in self.g.neighbors(net_node(net)):
            data = self.g.nodes[nb]
            comp = self.components.get(data["ref"])
            members.append(
                TracedConnection(
                    ref=data["ref"],
                    pin=data["pin"],
                    pin_name=data.get("pin_name"),
                    component_type=comp.type if comp else None,
                    value=comp.value if comp else None,
                )
            )
        return sorted(members, key=lambda m: (m.ref, m.pin))

    def neighbors_of(self, ref: str) -> set[str]:
        """Components sharing at least one net with ``ref``."""
        out: set[str] = set()
        for _, _, net in self.component_pins(ref):
            for m in self.net_members(net):
                if m.ref != ref:
                    out.add(m.ref)
        return out

    def nets_of(self, ref: str) -> set[str]:
        return {net for _, _, net in self.component_pins(ref)}

    def pin_net(self, ref: str, pin_or_name: str) -> str | None:
        for pin, name, net in self.component_pins(ref):
            if pin == pin_or_name or (name and name.upper() == pin_or_name.upper()):
                return net
        return None

    def ground_net(self) -> str | None:
        for name in self.nets:
            if is_ground(name):
                return name
        return None

    def resolve(self, target: str) -> ResolvedTarget:
        t = target.strip()
        if not t:
            return ResolvedTarget("unknown", None, target)
        if "." in t:
            ref, pin = t.split(".", 1)
            ref = ref.upper()
            if ref in self.components:
                for p, name, _ in self.component_pins(ref):
                    if p == pin or (name and name.upper() == pin.upper()):
                        return ResolvedTarget("pin", pin_node(ref, p), f"{ref}.{p}")
        up = t.upper()
        if up in self.components:
            return ResolvedTarget("component", comp_node(up), up)
        for name in self.nets:
            if name.upper() == up.lstrip("/"):
                return ResolvedTarget("net", net_node(name), name)
        return ResolvedTarget("unknown", None, t)

    def trace(self, target: str, to: str | None = None) -> TraceResult:
        resolved = self.resolve(target)
        if resolved.kind == "unknown":
            return TraceResult(
                target=target,
                kind="unknown",
                verified=True,
                provenance="parsed_netlist",
                uncertainty=(
                    f"'{target}' is not a component, pin or net in the verified netlist. "
                    f"Known components: {', '.join(sorted(self.components)) or 'none'}; "
                    f"nets: {', '.join(sorted(self.nets)) or 'none'}."
                ),
            )
        nets: list[TracedNet] = []
        if resolved.kind == "component":
            ref = resolved.label
            for pin, name, net in self.component_pins(ref):
                nets.append(
                    TracedNet(
                        net=net,
                        via_pin=pin,
                        via_pin_name=name,
                        connections=[m for m in self.net_members(net) if not (m.ref == ref and m.pin == pin)],
                    )
                )
        elif resolved.kind == "pin":
            data = self.g.nodes[resolved.node]
            net = data["net"]
            nets.append(
                TracedNet(
                    net=net,
                    via_pin=data["pin"],
                    via_pin_name=data.get("pin_name"),
                    connections=[
                        m for m in self.net_members(net) if not (m.ref == data["ref"] and m.pin == data["pin"])
                    ],
                )
            )
        else:
            nets.append(TracedNet(net=resolved.label, connections=self.net_members(resolved.label)))

        result = TraceResult(
            target=resolved.label, kind=resolved.kind, verified=True, provenance="parsed_netlist", nets=nets
        )
        if to:
            path, description, uncertainty = self.find_path(resolved, self.resolve(to))
            result.path = path
            result.path_description = description
            if uncertainty:
                result.uncertainty = uncertainty
        return result

    def find_path(
        self, src: ResolvedTarget, dst: ResolvedTarget
    ) -> tuple[list[str] | None, str | None, str | None]:
        if dst.kind == "unknown" or dst.node is None or src.node is None:
            return None, None, f"'{dst.label}' was not found in the verified netlist."
        view = self.g
        # Do not let the path cut straight through a component whose pins are the endpoints
        # (e.g. U1.OUT -> U1 -> U1.IN-): we want the external signal path.
        blocked = set()
        for t in (src, dst):
            if t.kind == "pin":
                blocked.add(comp_node(self.g.nodes[t.node]["ref"]))
        if blocked:
            view = nx.restricted_view(self.g, blocked, [])
        try:
            nodes = nx.shortest_path(view, src.node, dst.node)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None, None, f"No verified connection path between {src.label} and {dst.label}."
        steps: list[str] = []
        for n in nodes:
            kind, _, ident = n.partition(":")
            if kind == "pin":
                data = self.g.nodes[n]
                label = f"{data['ref']}.{data['pin']}"
                if data.get("pin_name"):
                    label += f" ({data['pin_name']})"
                steps.append(label)
            elif kind == "net":
                steps.append(f"net {ident}")
            else:
                comp = self.components.get(ident)
                steps.append(f"{ident}{f' [{comp.value}]' if comp and comp.value else ''}")
        return nodes, " → ".join(steps), None


def trace_unverified(analysis: CircuitAnalysis, target: str) -> TraceResult:
    """Trace result when no verified netlist exists: explicit uncertainty + inferred hints only."""
    hints: list[TracedNet] = []
    inferred = [n for n in analysis.nets if n.verification == "inferred"]
    up = target.strip().upper()
    ref = up.split(".", 1)[0]
    comps = {c.ref: c for c in analysis.components}
    for net in inferred:
        involved = any(node.ref == ref for node in net.nodes) or net.name.upper() == up
        if involved:
            hints.append(
                TracedNet(
                    net=net.name,
                    connections=[
                        TracedConnection(
                            ref=node.ref,
                            pin=node.pin,
                            pin_name=node.pin_name,
                            component_type=comps[node.ref].type if node.ref in comps else None,
                            value=comps[node.ref].value if node.ref in comps else None,
                        )
                        for node in net.nodes
                    ],
                )
            )
    if hints:
        uncertainty = (
            "No verified netlist is available for this circuit. The connections listed under "
            "'inferred_hint' were read from the schematic image by a vision model and may be wrong. "
            "Upload a KiCad netlist (.net) to get verified connectivity."
        )
    else:
        uncertainty = (
            "Connectivity data is unavailable: no verified netlist was provided and no image-inferred "
            f"connection involves '{target}'. Upload a KiCad netlist (.net) to enable tracing."
        )
    return TraceResult(target=target, kind="unknown", verified=False, uncertainty=uncertainty, inferred_hint=hints)
