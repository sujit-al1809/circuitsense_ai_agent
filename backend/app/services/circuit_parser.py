"""Netlist parsers.

``NetlistParser`` is the extension point; ``KiCadNetlistParser`` reads the
S-expression netlist written by KiCad's *File → Export → Netlist* (KiCad 5
"version D" unquoted tokens and KiCad 6+ "version E" quoted tokens).

Connectivity produced here is the only connectivity CircuitSense labels
``verified`` – it comes from a structured design file, not from looking at pixels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from app.schemas.circuit import REF_PATTERN, Component, ComponentPin, ComponentType, Net, NetNode

MAX_NETLIST_BYTES = 2 * 1024 * 1024
MAX_SEXPR_DEPTH = 64
MAX_COMPONENTS = 2000


class NetlistParseError(ValueError):
    pass


@dataclass
class ParsedNetlist:
    format: str
    components: list[Component]
    nets: list[Net]
    design_source: str | None = None
    warnings: list[str] = field(default_factory=list)


class NetlistParser(Protocol):
    format_name: str

    def can_parse(self, text: str) -> bool: ...

    def parse(self, text: str) -> ParsedNetlist: ...


# --------------------------------------------------------------------------- S-expressions

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)

SExpr = list["SExpr"] | str


def parse_sexpr(text: str) -> SExpr:
    stack: list[list[SExpr]] = []
    root: SExpr | None = None
    pos = 0
    length = len(text)
    while pos < length:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise NetlistParseError(f"unexpected character at offset {pos}")
        pos = m.end()
        if m.group(1):
            if len(stack) >= MAX_SEXPR_DEPTH:
                raise NetlistParseError("netlist nesting is too deep")
            new: list[SExpr] = []
            if stack:
                stack[-1].append(new)
            stack.append(new)
        elif m.group(2):
            if not stack:
                raise NetlistParseError(f"unbalanced ')' at offset {m.start()}")
            done = stack.pop()
            if not stack:
                if root is not None:
                    raise NetlistParseError("multiple top-level expressions")
                root = done
        elif m.group(3) is not None:
            if not stack:
                raise NetlistParseError("string outside of an expression")
            stack[-1].append(m.group(3).replace('\\"', '"').replace("\\\\", "\\"))
        elif m.group(4) is not None:
            if not stack:
                if m.group(4).strip():
                    raise NetlistParseError("token outside of an expression")
                continue
            stack[-1].append(m.group(4))
    if stack:
        raise NetlistParseError("unbalanced '(' – netlist is truncated")
    if root is None:
        raise NetlistParseError("empty netlist")
    return root


def _children(expr: SExpr, name: str) -> list[list[SExpr]]:
    if not isinstance(expr, list):
        return []
    return [c for c in expr if isinstance(c, list) and c and c[0] == name]


def _child(expr: SExpr, name: str) -> list[SExpr] | None:
    found = _children(expr, name)
    return found[0] if found else None


def _value(expr: SExpr, name: str) -> str | None:
    c = _child(expr, name)
    if c is None or len(c) < 2 or not isinstance(c[1], str):
        return None
    return c[1]


# --------------------------------------------------------------------------- type inference

_REF_PREFIX_TYPES: list[tuple[str, ComponentType]] = [
    ("LED", "led"),
    ("RV", "potentiometer"),
    ("VR", "potentiometer"),
    ("BT", "battery"),
    ("SW", "switch"),
    ("R", "resistor"),
    ("C", "capacitor"),
    ("L", "inductor"),
    ("D", "diode"),
    ("Q", "transistor"),
    ("U", "ic"),
    ("IC", "ic"),
    ("J", "connector"),
    ("P", "connector"),
    ("CN", "connector"),
    ("F", "fuse"),
    ("Y", "crystal"),
    ("X", "crystal"),
    ("V", "voltage_source"),
    ("I", "current_source"),
]

_OPAMP_HINTS = ("opamp", "op-amp", "op amp", "operational amplifier", "opamp_", "amplifier")
_VALID_TYPES = set(ComponentType.__args__)  # type: ignore[attr-defined]


def infer_component_type(ref: str, part: str | None, description: str | None, explicit: str | None) -> ComponentType:
    if explicit and explicit.lower() in _VALID_TYPES:
        return explicit.lower()  # type: ignore[return-value]
    hint = " ".join(x for x in (part, description) if x).lower()
    if "led" in hint.split() or (part or "").upper().startswith("LED"):
        return "led"
    if "zener" in hint:
        return "zener"
    if any(h in hint for h in _OPAMP_HINTS):
        return "opamp"
    if "mosfet" in hint:
        return "mosfet"
    if "vdc" in hint or "voltage source" in hint:
        return "voltage_source"
    if "battery" in hint:
        return "battery"
    letters = re.match(r"^[A-Za-z]+", ref)
    prefix = letters.group(0).upper() if letters else ""
    for p, t in _REF_PREFIX_TYPES:
        if prefix == p:
            return t
    for p, t in _REF_PREFIX_TYPES:
        if prefix.startswith(p):
            return t
    return "other"


# --------------------------------------------------------------------------- KiCad parser


class KiCadNetlistParser:
    format_name = "kicad-netlist"

    def can_parse(self, text: str) -> bool:
        return text.lstrip().startswith("(export")

    def parse(self, text: str) -> ParsedNetlist:
        if len(text.encode("utf-8", errors="ignore")) > MAX_NETLIST_BYTES:
            raise NetlistParseError("netlist file is too large")
        root = parse_sexpr(text)
        if not isinstance(root, list) or not root or root[0] != "export":
            raise NetlistParseError("not a KiCad netlist (missing top-level 'export')")

        warnings: list[str] = []
        design = _child(root, "design")
        design_source = _value(design, "source") if design else None

        comps_expr = _child(root, "components")
        nets_expr = _child(root, "nets")
        if comps_expr is None or nets_expr is None:
            raise NetlistParseError("netlist must contain 'components' and 'nets' sections")

        components: dict[str, dict] = {}
        for comp in _children(comps_expr, "comp"):
            ref = _value(comp, "ref")
            if not ref:
                raise NetlistParseError("component without a reference designator")
            if not REF_PATTERN.match(ref):
                raise NetlistParseError(f"invalid reference designator {ref!r}")
            ref = ref.upper()
            if ref in components:
                raise NetlistParseError(f"duplicate reference designator {ref}")
            libsource = _child(comp, "libsource")
            part = _value(libsource, "part") if libsource else None
            lib_desc = _value(libsource, "description") if libsource else None
            fields: dict[str, str] = {}
            for container in (_child(comp, "fields"),):
                for f in _children(container, "field") if container else []:
                    name = _value(f, "name")
                    if name and len(f) >= 3 and isinstance(f[-1], str):
                        fields[name] = f[-1]
            for prop in _children(comp, "property"):
                name, val = _value(prop, "name"), _value(prop, "value")
                if name and val is not None:
                    fields[name] = val
            description = _value(comp, "description") or lib_desc
            components[ref] = {
                "ref": ref,
                "value": _value(comp, "value"),
                "part": part,
                "description": description,
                "part_number": fields.get("MPN") or fields.get("PartNumber") or fields.get("Part Number"),
                "explicit_type": fields.get("CS_Type") or fields.get("Type"),
                "pins": {},
            }
            if len(components) > MAX_COMPONENTS:
                raise NetlistParseError(f"too many components (max {MAX_COMPONENTS})")

        nets: list[Net] = []
        seen_names: set[str] = set()
        for net in _children(nets_expr, "net"):
            name = _value(net, "name") or f"NET{_value(net, 'code') or len(nets) + 1}"
            name = name.lstrip("/") or name
            if name in seen_names:
                warnings.append(f"duplicate net name {name!r}; merged")
            nodes: list[NetNode] = []
            for node in _children(net, "node"):
                ref = (_value(node, "ref") or "").upper()
                pin = _value(node, "pin")
                if not ref or not pin:
                    raise NetlistParseError(f"net {name!r} has a node without ref/pin")
                if ref not in components:
                    raise NetlistParseError(f"net {name!r} references unknown component {ref}")
                pin_name = _value(node, "pinfunction")
                existing = components[ref]["pins"].get(pin)
                if existing and existing["net"] != name:
                    raise NetlistParseError(f"pin {ref}.{pin} appears on two nets ({existing['net']} and {name})")
                components[ref]["pins"][pin] = {"net": name, "name": pin_name}
                nodes.append(NetNode(ref=ref, pin=pin, pin_name=pin_name))
            if name in seen_names:
                for existing_net in nets:
                    if existing_net.name == name:
                        existing_net.nodes.extend(nodes)
                continue
            seen_names.add(name)
            nets.append(
                Net(name=name, nodes=nodes, verification="verified", provenance="parsed_netlist", confidence=1.0)
            )

        out_components: list[Component] = []
        for data in components.values():
            ctype = infer_component_type(data["ref"], data["part"], data["description"], data["explicit_type"])
            if not data["pins"]:
                warnings.append(f"{data['ref']} has no connected pins in the netlist")
            pins = [
                ComponentPin(pin=pin, name=info["name"], net=info["net"])
                for pin, info in sorted(data["pins"].items(), key=lambda kv: _pin_sort_key(kv[0]))
            ]
            out_components.append(
                Component(
                    ref=data["ref"],
                    type=ctype,
                    value=data["value"],
                    part_number=data["part_number"],
                    description=data["description"],
                    confidence=1.0,
                    provenance="parsed_netlist",
                    pins=pins,
                )
            )
        if not out_components:
            raise NetlistParseError("netlist contains no components")
        return ParsedNetlist(
            format=self.format_name,
            components=out_components,
            nets=nets,
            design_source=design_source,
            warnings=warnings,
        )


def _pin_sort_key(pin: str) -> tuple[int, str]:
    return (int(pin), "") if pin.isdigit() else (10_000, pin)


PARSERS: list[NetlistParser] = [KiCadNetlistParser()]


def looks_like_netlist(data: bytes) -> bool:
    head = data[:256].lstrip().decode("utf-8", errors="ignore")
    return any(p.can_parse(head) for p in PARSERS)


def parse_netlist(text: str) -> ParsedNetlist:
    for parser in PARSERS:
        if parser.can_parse(text):
            return parser.parse(text)
    raise NetlistParseError("unsupported netlist format; export a KiCad netlist (.net) from Eeschema")
