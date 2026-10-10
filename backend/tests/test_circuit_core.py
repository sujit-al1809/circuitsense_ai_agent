"""Schemas, netlist parsing, graph traversal, DC estimate and diagnostics."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.conftest import SAMPLES, sample_graph
from app.schemas.circuit import BoundingBox, Component, MeasurementCreate, Net, NetNode
from app.services.circuit_graph import CircuitGraph
from app.services.circuit_parser import NetlistParseError, parse_netlist, parse_sexpr
from app.services.dc_solver import solve_dc
from app.services.diagnostics import build_insight, find_issues, plan_next_check
from app.schemas.circuit import Measurement
from datetime import datetime, timezone

# --------------------------------------------------------------------------- schemas


def test_component_schema_validation():
    c = Component(ref="r1", type="resistor", value="330", confidence=0.9, provenance="image_inference")
    assert c.ref == "R1"
    with pytest.raises(ValidationError):
        Component(ref="R 1; DROP", type="resistor", confidence=0.5, provenance="image_inference")
    with pytest.raises(ValidationError):
        Component(ref="R1", type="flux_capacitor", confidence=0.5, provenance="image_inference")
    with pytest.raises(ValidationError):
        Component(ref="R1", type="resistor", confidence=1.5, provenance="image_inference")


def test_bounding_box_must_fit_page():
    BoundingBox(x=0.5, y=0.5, width=0.5, height=0.5, source="image_inference")
    with pytest.raises(ValidationError):
        BoundingBox(x=0.8, y=0.1, width=0.5, height=0.1, source="image_inference")


def test_inferred_connectivity_can_never_be_verified():
    with pytest.raises(ValidationError):
        Net(name="N1", nodes=[NetNode(ref="R1", pin="1")], verification="verified", provenance="image_inference")
    Net(name="N1", nodes=[NetNode(ref="R1", pin="1")], verification="inferred", provenance="image_inference")


def test_measurement_must_be_finite():
    with pytest.raises(ValidationError):
        MeasurementCreate(quantity="voltage", value=float("nan"), unit="V", target="VCC")


# --------------------------------------------------------------------------- parser

# Independently written expected connectivity: (net -> set of "REF.PIN") for each sample.
EXPECTED_CONNECTIVITY = {
    "led_basic": {
        "VCC": {"V1.1", "R1.1"},
        "LED_A": {"R1.2", "D1.2"},
        "GND": {"D1.1", "V1.2"},
    },
    "resistor_divider": {
        "VIN": {"V1.1", "R1.1"},
        "VOUT": {"R1.2", "R2.1", "J1.1"},
        "GND": {"R2.2", "J1.2", "V1.2"},
    },
    "noninverting_amp": {
        "VIN": {"V1.1", "U1.3"},
        "FB": {"U1.2", "R1.1", "R2.1"},
        "VOUT": {"U1.6", "R2.2", "R3.1"},
        "VCC": {"V2.1", "U1.7"},
        "VEE": {"V3.2", "U1.4"},
        "GND": {"V1.2", "V2.2", "V3.1", "R1.2", "R3.2"},
    },
}


@pytest.mark.parametrize("sample_id", sorted(EXPECTED_CONNECTIVITY))
def test_reference_netlists_match_expected_connectivity(sample_id):
    parsed = parse_netlist((SAMPLES / sample_id / f"{sample_id}.net").read_text(encoding="utf-8"))
    actual = {n.name: {f"{x.ref}.{x.pin}" for x in n.nodes} for n in parsed.nets}
    assert actual == EXPECTED_CONNECTIVITY[sample_id]
    assert all(n.verification == "verified" and n.provenance == "parsed_netlist" for n in parsed.nets)
    assert parsed.warnings == []


def test_reference_annotations_cover_netlist_components():
    import json

    for sample_id in EXPECTED_CONNECTIVITY:
        parsed = parse_netlist((SAMPLES / sample_id / f"{sample_id}.net").read_text(encoding="utf-8"))
        ann = json.loads((SAMPLES / sample_id / "annotations.json").read_text(encoding="utf-8"))
        assert set(ann["components"]) == {c.ref for c in parsed.components}
        for box in ann["components"].values():
            BoundingBox(source="sample_annotation", **box)


def test_parses_kicad5_unquoted_netlist():
    text = """(export (version D)
      (components
        (comp (ref R1) (value 1k) (libsource (lib Device) (part R)))
        (comp (ref C1) (value 100n) (libsource (lib Device) (part C))))
      (nets
        (net (code 1) (name /IN) (node (ref R1) (pin 1)))
        (net (code 2) (name GND) (node (ref R1) (pin 2)) (node (ref C1) (pin 2)))
        (net (code 3) (name "Net-(C1-Pad1)") (node (ref C1) (pin 1)))))"""
    parsed = parse_netlist(text)
    types = {c.ref: c.type for c in parsed.components}
    assert types == {"R1": "resistor", "C1": "capacitor"}
    assert {n.name for n in parsed.nets} == {"IN", "GND", "Net-(C1-Pad1)"}


@pytest.mark.parametrize(
    "text,message",
    [
        ("(export (components (comp (ref R1)) (comp (ref R1))) (nets))", "duplicate"),
        ("(export (components (comp (ref R1))) (nets (net (name A) (node (ref R9) (pin 1)))))", "unknown component"),
        ("(export (components (comp (ref R1))) (nets (net (name A) (node (ref R1) (pin 1))) (net (name B) (node (ref R1) (pin 1)))))", "two nets"),
        ("(export (components (comp (ref R1)))", "truncated"),
        ("(export (design))", "must contain"),
        ("(kicad_sch (version 1))", "unsupported"),
    ],
)
def test_netlist_errors(text, message):
    with pytest.raises(NetlistParseError, match=message):
        parse_netlist(text)


def test_sexpr_depth_limit():
    with pytest.raises(NetlistParseError):
        parse_sexpr("(" * 200 + ")" * 200)


# --------------------------------------------------------------------------- graph


def test_trace_component():
    g = sample_graph("led_basic")
    t = g.trace("R1")
    assert t.verified and t.kind == "component"
    by_net = {n.net: {c.ref for c in n.connections} for n in t.nets}
    assert by_net == {"VCC": {"V1"}, "LED_A": {"D1"}}


def test_trace_pin_by_name_and_number():
    g = sample_graph("noninverting_amp")
    assert g.trace("U1.OUT").nets[0].net == "VOUT"
    assert g.trace("u1.6").nets[0].net == "VOUT"
    assert {c.ref for c in g.trace("U1.IN-").nets[0].connections} == {"R1", "R2"}


def test_trace_net_and_path():
    g = sample_graph("resistor_divider")
    t = g.trace("vout")
    assert t.kind == "net"
    assert {c.ref for c in t.nets[0].connections} == {"R1", "R2", "J1"}
    p = g.trace("VIN", "J1.1")
    assert p.path_description == "net VIN → R1.1 → R1 [10k] → R1.2 → net VOUT → J1.1 (Pin_1)"
    # The shortest path between two components may legitimately run through ground.
    assert "net GND" in g.trace("V1", "J1").path_description


def test_feedback_path_does_not_shortcut_through_the_opamp():
    g = sample_graph("noninverting_amp")
    t = g.trace("U1.OUT", "U1.IN-")
    assert "comp:U1" not in t.path
    assert "R2" in t.path_description


def test_trace_unknown_target():
    t = sample_graph("led_basic").trace("Q42")
    assert t.kind == "unknown"
    assert "not a component" in t.uncertainty


def test_graph_from_inferred_nets_is_empty():
    nets = [Net(name="N1", nodes=[NetNode(ref="R1", pin="?"), NetNode(ref="D1", pin="?")], verification="inferred", provenance="image_inference")]
    comps = [Component(ref="R1", type="resistor", confidence=0.6, provenance="image_inference"), Component(ref="D1", type="led", confidence=0.6, provenance="image_inference")]
    from app.schemas.circuit import CircuitAnalysis

    a = CircuitAnalysis(circuit_id="c", created_at=datetime.now(timezone.utc), mode="demo", source_kind="image", title="t", circuit_type="x", summary="", components=comps, nets=nets, connectivity_status="inferred")
    assert CircuitGraph.from_analysis(a).is_empty
    from app.services.circuit_graph import trace_unverified

    t = trace_unverified(a, "R1")
    assert t.verified is False and len(t.inferred_hint) == 1
    assert "may be wrong" in t.uncertainty


# --------------------------------------------------------------------------- DC estimate + diagnostics


def test_dc_estimates_for_samples():
    led = solve_dc(sample_graph("led_basic"))
    assert led.node_voltages["VCC"] == pytest.approx(5.0)
    assert led.node_voltages["LED_A"] == pytest.approx(2.0, abs=1e-6)
    assert led.component_currents["R1"] == pytest.approx(3 / 330, rel=1e-6)
    div = solve_dc(sample_graph("resistor_divider"))
    assert div.node_voltages["VOUT"] == pytest.approx(12 * 4.7 / 14.7, rel=1e-6)
    amp = solve_dc(sample_graph("noninverting_amp"))
    assert amp.node_voltages["VOUT"] == pytest.approx(1.1, rel=1e-4)
    assert amp.node_voltages["FB"] == pytest.approx(0.1, rel=1e-4)
    assert amp.node_voltages["VEE"] == pytest.approx(-12.0)


def test_reversed_led_is_off():
    text = (SAMPLES / "led_basic" / "led_basic.net").read_text(encoding="utf-8")
    text = text.replace('(pinfunction "A")', '(pinfunction "TMP")').replace('(pinfunction "K")', '(pinfunction "A")').replace('(pinfunction "TMP")', '(pinfunction "K")')
    parsed = parse_netlist(text)
    dc = solve_dc(CircuitGraph(parsed.components, parsed.nets))
    assert dc.diode_states["D1"] is False
    assert dc.component_currents["R1"] == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize(
    "sample_id,ctype,key",
    [("led_basic", "led_indicator", {"source": "V1", "resistor": "R1", "led": "D1"}),
     ("resistor_divider", "resistor_divider", {"r_top": "R1", "r_bottom": "R2"}),
     ("noninverting_amp", "non_inverting_amplifier", {"opamp": "U1", "rf": "R2", "rg": "R1"})],
)
def test_topology_classification(sample_id, ctype, key):
    ins = build_insight(sample_graph(sample_id))
    assert ins.circuit_type == ctype
    for k, v in key.items():
        assert ins.key[k] == v


def test_led_without_resistor_is_flagged():
    text = """(export (version "E") (components
        (comp (ref "V1") (value "5V") (property (name "CS_Type") (value "voltage_source")))
        (comp (ref "D1") (value "LED") (libsource (lib "Device") (part "LED"))))
      (nets (net (code "1") (name "VCC") (node (ref "V1") (pin "1") (pinfunction "+")) (node (ref "D1") (pin "2") (pinfunction "A")))
            (net (code "2") (name "GND") (node (ref "V1") (pin "2") (pinfunction "-")) (node (ref "D1") (pin "1") (pinfunction "K")))))"""
    parsed = parse_netlist(text)
    g = CircuitGraph(parsed.components, parsed.nets)
    issues = find_issues(g, build_insight(g))
    issue = next(i for i in issues if i.id == "led-no-resistor-D1")
    assert issue.severity == "critical" and issue.status == "confirmed"
    assert issue.evidence and issue.evidence[0].kind == "parsed_netlist"


def _m(target, value, quantity="voltage", unit="V"):
    from app.services.units import unit_to_si

    si, su = unit_to_si(value, unit, quantity)
    return Measurement(id=f"m{target}{value}", circuit_id="c", recorded_at=datetime.now(timezone.utc), quantity=quantity, value=value, unit=unit, target=target, si_value=si, si_unit=su)


def test_planner_progression_divider():
    g = sample_graph("resistor_divider")
    ins = build_insight(g)
    first = plan_next_check(g, ins, [])
    assert first.next_action.target == "VIN"
    second = plan_next_check(g, ins, [_m("VIN", 12.0)])
    assert second.next_action.target == "VOUT"
    assert second.confirmed and "within" in second.confirmed[0].statement
    low = plan_next_check(g, ins, [_m("VIN", 12.0), _m("VOUT", 3.0)])
    assert low.next_action.target == "R2"
    assert low.next_action.safety_note and "Disconnect the supply" in low.next_action.safety_note
    assert any("outside" in f.statement for f in low.confirmed)


def test_planner_stops_at_bad_supply():
    g = sample_graph("led_basic")
    plan = plan_next_check(g, build_insight(g), [_m("V1.1", 3.3)])
    assert "open-circuit voltage" in plan.next_action.action
    assert any("upstream" in h.statement for h in plan.hypotheses)


def test_high_voltage_safety_note():
    text = (SAMPLES / "resistor_divider" / "resistor_divider.net").read_text(encoding="utf-8").replace('"12V"', '"230V"')
    parsed = parse_netlist(text)
    g = CircuitGraph(parsed.components, parsed.nets)
    plan = plan_next_check(g, build_insight(g), [])
    assert "Hazardous voltage" in plan.next_action.safety_note
