"""Deterministic, rule-based agent policy used in demo mode.

It plans tool calls from the question with explicit rules, lets the orchestrator
execute the real tools, and composes the answer *only* from tool outputs and the
verified circuit data. It never calls a model and is always labelled as demo mode.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.schemas.chat import Finding
from app.schemas.circuit import DiagnosticAction
from app.services.agent import AgentState, FinalAnswer, ModelTurn, ToolCall, ToolResultRecord
from app.services.circuit_graph import is_ground
from app.services.diagnostics import plan_next_check
from app.services.units import format_quantity

_REF_TOKEN = re.compile(r"\b([A-Za-z]{1,3}\d{1,3})(?:\.([A-Za-z0-9+\-]+))?\b")
_MEAS = re.compile(
    r"(?P<num>[-+]?\d+(?:\.\d+)?)\s*(?P<unit>mV|V|volts?|uA|µA|mA|A|amps?|kΩ|kohms?|k|MΩ|Ω|ohms?)(?![A-Za-z])",
    re.I,
)
_MEAS_TRIGGER = re.compile(r"\b(measur\w*|reading|reads|read|got|getting|shows?|showing|see|seeing)\b", re.I)
_UNIT_QUANTITY = {
    "mv": ("voltage", "mV"), "v": ("voltage", "V"), "volt": ("voltage", "V"), "volts": ("voltage", "V"),
    "ua": ("current", "uA"), "µa": ("current", "uA"), "ma": ("current", "mA"), "a": ("current", "A"),
    "amp": ("current", "A"), "amps": ("current", "A"),
    "kω": ("resistance", "kΩ"), "kohm": ("resistance", "kΩ"), "kohms": ("resistance", "kΩ"), "k": ("resistance", "kΩ"),
    "mω": ("resistance", "MΩ"), "ω": ("resistance", "Ω"), "ohm": ("resistance", "Ω"), "ohms": ("resistance", "Ω"),
}

ROLE_WORDS = re.compile(r"\b(role|purpose|what does .* do|why is (this|it|.+) (needed|there|used|required)|what is .* for|function of|job of)\b", re.I)
DEBUG_WORDS = re.compile(
    r"\b(low|high|dim|dark|not (working|lighting|light|on)|doesn'?t|does not|isn'?t|wrong|fault|faulty|broken|investigate|"
    r"troubleshoot|debug|diagnos\w*|measure next|what should i (measure|check)|next (check|step|measurement)|problem|issue|zero|no output)\b",
    re.I,
)
CALC_WORDS = re.compile(r"\b(calculate|compute|how much|current|gain|output voltage|power|dissipat\w*|voltage (at|on|across))\b", re.I)
TRACE_WORDS = re.compile(r"\b(trace|connected|connection|connections|path|feedback|follow|net)\b", re.I)
SIM_WORDS = re.compile(r"\b(simulat\w*|spice|ngspice)\b", re.I)
DATASHEET_WORDS = re.compile(r"\b(datasheet|spec\w*|rating|rated|maximum|max|minimum|forward voltage|pinout|pin ?out|tolerance|colou?r code)\b", re.I)
EXPLAIN_WORDS = re.compile(r"\b(explain|how does|how it works|what does (this|the) circuit|overview|summar\w*|what is this)\b", re.I)

TYPE_WORDS = {
    "led": ("led",),
    "resistor": ("resistor",),
    "opamp": ("op-amp", "opamp", "op amp", "amplifier"),
    "voltage_source": ("supply", "source", "battery"),
    "connector": ("connector", "header"),
}

CALC_TITLES = {
    "ohms_law": "Ohm's law",
    "voltage_divider": "Voltage divider",
    "led_series_resistor": "LED series resistor",
    "series_resistance": "Series resistance",
    "parallel_resistance": "Parallel resistance",
    "non_inverting_gain": "Non-inverting amplifier gain",
    "inverting_gain": "Inverting amplifier gain",
    "rc_time_constant": "RC time constant",
}

DEMO_LABEL = "_Demo mode: deterministic rule-based response composed from CircuitSense tools — no AI model was called._"


@dataclass
class Intent:
    kind: str
    ref: str | None = None
    unknown_refs: list[str] = field(default_factory=list)
    net: str | None = None
    pin_target: str | None = None
    calls: list[ToolCall] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


def _call(name: str, **kwargs: Any) -> ToolCall:
    return ToolCall(id=f"demo_{uuid.uuid4().hex[:8]}", name=name, input=kwargs)


class DemoAgentPolicy:
    mode = "demo"

    def next_turn(self, state: AgentState) -> ModelTurn:
        intent = self.plan(state)
        if not state.steps and intent.calls:
            return ModelTurn(tool_calls=intent.calls)
        results = [r for step in state.steps for r in step.results]
        return ModelTurn(final=self.compose(state, intent, results))

    # ------------------------------------------------------------------ planning
    def _mentions(self, state: AgentState) -> tuple[str | None, list[str], str | None, str | None]:
        analysis = state.session.analysis
        known = {c.ref for c in analysis.components}
        ref, unknown, pin_target = None, [], None
        for m in _REF_TOKEN.finditer(state.message):
            token = m.group(1).upper()
            if token in known:
                ref = ref or token
                if m.group(2) and pin_target is None:
                    pin_target = f"{token}.{m.group(2)}"
            elif re.match(r"^(R|C|L|D|Q|U|J|V|BT|SW|F|IC|LED)\d", token):
                unknown.append(token)
        net = None
        for n in analysis.nets:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(n.name)}(?![A-Za-z0-9_])", state.message, re.I):
                if net is None or len(n.name) > len(net):
                    net = n.name
        if ref is None:
            lower = state.message.lower()
            for ctype, words in TYPE_WORDS.items():
                if any(re.search(rf"\b{re.escape(w)}\b", lower) for w in words):
                    matches = [c.ref for c in analysis.components if c.type == ctype or (ctype == "voltage_source" and c.type == "battery")]
                    if len(matches) == 1:
                        ref = matches[0]
                        break
        return ref, unknown, net, pin_target

    def plan(self, state: AgentState) -> Intent:
        msg = state.message
        ref, unknown, net, pin_target = self._mentions(state)
        explicit_ref = ref
        ref = ref or (state.focus.upper() if state.focus else None)
        insight = state.insight

        # 1. A reported measurement
        meas = _MEAS.search(msg)
        if meas and _MEAS_TRIGGER.search(msg) and (net or explicit_ref or pin_target):
            quantity, unit = _UNIT_QUANTITY[meas.group("unit").lower()]
            if quantity == "resistance" or re.search(r"\bacross\b", msg, re.I):
                target = explicit_ref or pin_target or net
            else:
                target = pin_target or net or explicit_ref
            data = {
                "quantity": quantity,
                "value": float(meas.group("num")),
                "unit": unit,
                "target": target,
                "power_state": "unpowered" if quantity == "resistance" else "powered",
            }
            if quantity == "voltage" and target in {n.name for n in state.session.analysis.nets} and not is_ground(str(target)):
                data["reference"] = (state.graph.ground_net() if state.graph else None) or "GND"
            return Intent("measurement", ref=ref, calls=[_call("record_measurement", **data)], data=data)

        # 2. Simulation
        if SIM_WORDS.search(msg):
            return Intent("simulate", ref=ref, calls=[_call("simulate_circuit", analysis="dc_operating_point")])

        # 3. Tracing
        if TRACE_WORDS.search(msg) and not ROLE_WORDS.search(msg):
            if re.search(r"\bfeedback\b", msg, re.I) and insight and insight.key.get("opamp"):
                amp = insight.key["opamp"]
                return Intent("trace_path", ref=amp, calls=[_call("trace_net", target=f"{amp}.OUT", to=f"{amp}.IN-")])
            target = pin_target or explicit_ref or net or ref
            if target:
                return Intent("trace", ref=ref, net=net, calls=[_call("trace_net", target=target)])

        # 4. Component role
        if ref and (ROLE_WORDS.search(msg) or (not DEBUG_WORDS.search(msg) and not CALC_WORDS.search(msg) and not DATASHEET_WORDS.search(msg) and not EXPLAIN_WORDS.search(msg))):
            if unknown and not explicit_ref:
                return Intent("unknown_ref", unknown_refs=unknown)
            return Intent("role", ref=ref, calls=[_call("get_component_info", ref=ref, max_passages=2), _call("trace_net", target=ref)])
        if unknown and not explicit_ref and ROLE_WORDS.search(msg):
            return Intent("unknown_ref", unknown_refs=unknown)

        # 5. Debugging
        if DEBUG_WORDS.search(msg):
            calls = []
            focus_ref = ref or self._symptom_component(state)
            if focus_ref:
                calls.append(_call("get_component_info", ref=focus_ref, query="troubleshooting " + msg[:120], max_passages=2))
            return Intent("debug", ref=focus_ref, calls=calls)

        # 6. Datasheet lookup
        if DATASHEET_WORDS.search(msg):
            kwargs: dict[str, Any] = {"query": msg[:300], "max_passages": 3}
            if ref:
                kwargs["ref"] = ref
            return Intent("datasheet", ref=ref, calls=[_call("get_component_info", **kwargs)])

        # 7. Calculation
        if CALC_WORDS.search(msg):
            calc = self._calc_for(state, msg)
            if calc:
                return Intent("calculate", ref=ref, calls=[_call("calculate_electrical_values", **calc)])
            return Intent("calc_missing", ref=ref)

        # 8. Explain the circuit
        if EXPLAIN_WORDS.search(msg) or not ref:
            calls = []
            calc = self._calc_for(state, "")
            if calc:
                calls.append(_call("calculate_electrical_values", **calc))
            return Intent("explain" if EXPLAIN_WORDS.search(msg) else "fallback", calls=calls)
        return Intent("fallback")

    @staticmethod
    def _symptom_component(state: AgentState) -> str | None:
        ins = state.insight
        if not ins:
            return None
        return ins.key.get("led") or ins.key.get("opamp") or ins.key.get("r_bottom")

    @staticmethod
    def _calc_for(state: AgentState, msg: str) -> dict[str, Any] | None:
        ins = state.insight
        if not ins:
            return None
        comps = state.session.analysis.components
        by_ref = {c.ref: c for c in comps}
        try:
            if ins.circuit_type == "led_indicator":
                for calc in ins.calculations:
                    if calc.calculation == "led_series_resistor":
                        return {"calculation": "led_series_resistor", "params": {k: v.value for k, v in calc.inputs.items()}}
            if ins.circuit_type == "resistor_divider":
                for calc in ins.calculations:
                    if calc.calculation == "voltage_divider":
                        params = {k: v.value for k, v in calc.inputs.items()}
                        load = re.search(r"(\d+(?:\.\d+)?\s*[kKM]?)\s*(?:Ω|ohm|ohms)?\s*load", msg)
                        if load:
                            params["r_load"] = load.group(1).replace(" ", "")
                        return {"calculation": "voltage_divider", "params": params}
            if ins.circuit_type == "non_inverting_amplifier":
                for calc in ins.calculations:
                    if calc.calculation == "non_inverting_gain":
                        return {"calculation": "non_inverting_gain", "params": {k: v.value for k, v in calc.inputs.items()}}
            if ins.circuit_type == "inverting_amplifier":
                rf, rin = by_ref[ins.key["rf"]], by_ref[ins.key["rin"]]
                return {"calculation": "inverting_gain", "params": {"rf": rf.value, "rin": rin.value}}
        except KeyError:
            return None
        return None

    # ------------------------------------------------------------------ composing
    def compose(self, state: AgentState, intent: Intent, results: list[ToolResultRecord]) -> FinalAnswer:
        handler = getattr(self, f"_compose_{intent.kind}", self._compose_fallback)
        final: FinalAnswer = handler(state, intent, results)
        final.answer = f"{final.answer}\n\n{DEMO_LABEL}"
        return final

    @staticmethod
    def _result(results: list[ToolResultRecord], name: str) -> ToolResultRecord | None:
        return next((r for r in results if r.call.name == name), None)

    def _no_connectivity_note(self, state: AgentState) -> str:
        a = state.session.analysis
        if a.connectivity_status == "inferred":
            return "Connectivity for this circuit was only **inferred from the image** and is not verified."
        return (
            "There is **no verified connectivity** for this circuit (no netlist was provided"
            + (" and demo mode does not interpret images" if a.mode == "demo" else "")
            + "). Upload a KiCad netlist (.net) or load a bundled sample."
        )

    def _planner(self, state: AgentState):
        if state.graph is None or state.insight is None:
            return None
        return plan_next_check(state.graph, state.insight, state.session.measurements)

    def _compose_measurement(self, state: AgentState, intent: Intent, results: list[ToolResultRecord]) -> FinalAnswer:
        r = self._result(results, "record_measurement")
        if r is None or r.output.status != "ok":
            err = r.output.data.get("error") if r else "not recorded"
            return FinalAnswer(answer=f"I could not record that measurement: {err}", follow_up_questions=["Which net or component did you measure, and in which unit?"])
        m = r.output.measurements[0]
        where = f"{m.target}" + (f" (relative to {m.reference})" if m.reference else "")
        lines = [f"Recorded **{m.quantity} = {format_quantity(m.si_value, m.si_unit)}** at {where}."]
        plan = self._planner(state)
        if plan is None:
            lines.append(self._no_connectivity_note(state) + " I can't compare it against an expected value.")
            return FinalAnswer(answer="\n\n".join(lines))
        latest = [e for e in plan.evaluations if e.measurement.id == m.id]
        if latest and latest[0].deviates is not None:
            e = latest[0]
            verdict = "**outside**" if e.deviates else "within"
            lines.append(f"That is {verdict} the expected range: {e.detail}.")
        elif not latest:
            lines.append("I have no expected value for that point yet, so I stored it without a comparison.")
        if plan.next_action:
            lines.append(f"**Next check:** {plan.next_action.action}\n\n_Why:_ {plan.next_action.reason}")
        return FinalAnswer(answer="\n\n".join(lines), confirmed_findings=plan.confirmed, hypotheses=plan.hypotheses, next_step=plan.next_action)

    def _compose_simulate(self, state: AgentState, intent: Intent, results: list[ToolResultRecord]) -> FinalAnswer:
        r = self._result(results, "simulate_circuit")
        if r and r.output.status == "ok":
            volts = r.output.data.get("node_voltages", {})
            lines = ["**ngspice DC operating point:**"] + [f"- V({k}) = {format_quantity(v, 'V')}" for k, v in volts.items()]
            lines.append("\n_Assumptions:_ " + "; ".join(r.output.data.get("assumptions", [])))
            return FinalAnswer(
                answer="\n".join(lines),
                confirmed_findings=[Finding(statement=f"Simulated V({k}) = {format_quantity(v, 'V')}", basis="simulation") for k, v in list(volts.items())[:6]],
            )
        reason = (r.output.data.get("error") if r else None) or "simulation unavailable"
        lines = [f"**Simulation unavailable.** {reason}"]
        if state.insight and state.insight.dc:
            dc = state.insight.dc
            lines.append("\nInstead, here is CircuitSense's **idealised DC estimate** (a deterministic calculation, *not* an ngspice simulation):")
            lines += [f"- V({k}) = {format_quantity(v, 'V')}" for k, v in dc.node_voltages.items()]
            lines.append("\n_Assumptions:_ " + " ".join(dc.assumptions))
        return FinalAnswer(answer="\n".join(lines), follow_up_questions=["Install ngspice and set NGSPICE_PATH to enable real simulation."])

    def _format_trace(self, data: dict[str, Any]) -> list[str]:
        lines = []
        for n in data.get("nets", []):
            via = f" via pin {n['via_pin']}" + (f" ({n['via_pin_name']})" if n.get("via_pin_name") else "") if n.get("via_pin") else ""
            conns = ", ".join(
                f"{c['ref']}.{c['pin']}" + (f" ({c['pin_name']})" if c.get("pin_name") else "") + (f" [{c['value']}]" if c.get("value") else "")
                for c in n.get("connections", [])
            ) or "nothing else"
            lines.append(f"- **{n['net']}**{via} → {conns}")
        return lines

    def _compose_trace(self, state: AgentState, intent: Intent, results: list[ToolResultRecord]) -> FinalAnswer:
        r = self._result(results, "trace_net")
        if r is None:
            return self._compose_fallback(state, intent, results)
        if r.output.status == "unavailable":
            lines = [self._no_connectivity_note(state), r.output.data.get("uncertainty", "")]
            hints = r.output.data.get("inferred_hint", [])
            if hints:
                lines.append("**Unverified hints from the image:**\n" + "\n".join(self._format_trace({"nets": hints})))
            return FinalAnswer(answer="\n\n".join(x for x in lines if x), hypotheses=[Finding(statement="Connections shown are inferred from the image only.", basis="image_inference")] if hints else [])
        if r.output.status == "error":
            return FinalAnswer(answer=r.output.data.get("uncertainty") or r.output.data.get("error", "Trace failed."))
        target = r.output.data.get("target")
        lines = [f"**Verified connections of {target}** (from the parsed netlist):"] + self._format_trace(r.output.data)
        confirmed = []
        for n in r.output.data.get("nets", []):
            others = ", ".join(sorted({c["ref"] for c in n.get("connections", [])})) or "no other parts"
            if r.output.data.get("kind") == "net":
                statement = f"Net {n['net']} connects {others}."
            else:
                pin = f" pin {n['via_pin']}" if n.get("via_pin") else ""
                statement = f"{target}{pin} is on net {n['net']} together with {others}."
            confirmed.append(Finding(statement=statement, basis="parsed_netlist"))
        return FinalAnswer(answer="\n".join(lines), confirmed_findings=confirmed)

    def _compose_trace_path(self, state: AgentState, intent: Intent, results: list[ToolResultRecord]) -> FinalAnswer:
        r = self._result(results, "trace_net")
        if r is None or r.output.status != "ok" or not r.output.data.get("path_description"):
            return self._compose_trace(state, intent, results)
        path = r.output.data["path_description"]
        ins = state.insight
        lines = [f"**Feedback path (verified):** {path}"]
        if ins and ins.key.get("rf"):
            rf, other = ins.key["rf"], ins.key.get("rg") or ins.key.get("rin")
            lines.append(
                f"{rf} returns a fraction of the output to the inverting input; {other} completes the feedback divider "
                f"to {'ground' if ins.key.get('rg') else 'the input'}. That ratio sets the closed-loop gain"
                + (f" ({ins.calculations[0].outputs['gain'].display})." if ins.calculations else ".")
            )
            lines.append(f"If the feedback path were open, {ins.key['opamp']} would run open-loop and its output would slam to a rail.")
        return FinalAnswer(
            answer="\n\n".join(lines),
            confirmed_findings=[Finding(statement=f"Verified feedback path: {path}", basis="parsed_netlist")],
            follow_up_questions=["Do you want to check the feedback path with measurements?"],
        )

    def _compose_role(self, state: AgentState, intent: Intent, results: list[ToolResultRecord]) -> FinalAnswer:
        info = self._result(results, "get_component_info")
        trace = self._result(results, "trace_net")
        if info is None or info.output.status != "ok":
            return FinalAnswer(answer=(info.output.data.get("error") if info else "Component not found.") + f". Components in this circuit: {info.output.data.get('known_components', '') if info else ''}")
        comp = info.output.data["component"]
        role = comp.get("role_from_verified_topology")
        lines = [f"**{comp['ref']}** — {comp['type'].replace('_', ' ')}" + (f", marked **{comp['value']}**" if comp.get("value") else ", value not marked")]
        confirmed: list[Finding] = []
        hypotheses: list[Finding] = []
        if role:
            lines.append(f"**Role:** {role}.")
            confirmed.append(Finding(statement=f"{comp['ref']}: {role}", basis="parsed_netlist", detail="Role derived by rule from the verified netlist topology."))
        else:
            lines.append(self._no_connectivity_note(state))
        if trace and trace.output.status == "ok":
            lines.append("**Connections:**\n" + "\n".join(self._format_trace(trace.output.data)))
        if state.insight and comp["ref"] in state.insight.key.values():
            for calc in state.insight.calculations:
                if calc.calculation == "led_series_resistor" and comp["ref"] in (state.insight.key.get("resistor"), state.insight.key.get("led")):
                    lines.append(
                        f"Without {state.insight.key['resistor']}, the LED would be connected straight across the supply and the current would be "
                        f"limited only by the supply's internal resistance — typically enough to destroy the LED. With it, the current is about "
                        f"**{calc.outputs['current'].display}** (assuming Vf = 2.0 V)."
                    )
                    confirmed.append(Finding(statement=f"Estimated LED current {calc.outputs['current'].display}", basis="calculation", detail=calc.formula))
                if calc.calculation == "non_inverting_gain" and comp["ref"] in (state.insight.key.get("rf"), state.insight.key.get("rg")):
                    lines.append(f"Together {state.insight.key['rf']} and {state.insight.key['rg']} set the gain: {calc.formula} = **{calc.outputs['gain'].display}**.")
                    confirmed.append(Finding(statement=f"Closed-loop gain {calc.outputs['gain'].display}", basis="calculation", detail=calc.formula))
                if calc.calculation == "voltage_divider" and comp["ref"] in (state.insight.key.get("r_top"), state.insight.key.get("r_bottom")):
                    lines.append(f"The divider sets the output to about **{calc.outputs['vout'].display}** ({calc.formula}).")
        if info.output.citations:
            top = info.output.citations[0]
            label = " (illustrative reference)" if top.illustrative and "illustrative" not in top.title.lower() else ""
            lines.append(f"From _{top.title}_{label}, section “{top.section}”: {self._first_sentences(top.excerpt)}")
        next_step = None
        if DEBUG_WORDS.search(state.message):
            plan = self._planner(state)
            next_step = plan.next_action if plan else None
        return FinalAnswer(
            answer="\n\n".join(lines),
            confirmed_findings=confirmed,
            hypotheses=hypotheses,
            next_step=next_step,
            cited_passage_ids=[c.id for c in info.output.citations],
            follow_up_questions=[f"What should I measure to check {comp['ref']}?", f"Show the datasheet limits for {comp['ref']}."],
        )

    def _compose_unknown_ref(self, state: AgentState, intent: Intent, results: list[ToolResultRecord]) -> FinalAnswer:
        known = ", ".join(c.ref for c in state.session.analysis.components) or "none"
        return FinalAnswer(
            answer=f"{', '.join(intent.unknown_refs)} is not part of this circuit. The components I know about are: {known}.",
            follow_up_questions=[f"What is the role of {c.ref}?" for c in state.session.analysis.components[:2]],
        )

    def _compose_debug(self, state: AgentState, intent: Intent, results: list[ToolResultRecord]) -> FinalAnswer:
        plan = self._planner(state)
        if plan is None:
            return FinalAnswer(
                answer=self._no_connectivity_note(state) + " Without it I can't compute expected values or pick a meaningful next measurement.",
                follow_up_questions=["Can you upload the KiCad netlist (.net) for this board?", "Which supply voltage does the circuit run from?"],
            )
        ins = state.insight
        assert ins is not None
        lines = []
        if ins.expected_behavior:
            lines.append(f"**Expected behaviour:** {ins.expected_behavior}")
        if plan.confirmed:
            lines.append("**What your measurements show:**\n" + "\n".join(f"- {f.statement}" for f in plan.confirmed))
        if plan.hypotheses:
            lines.append("**Possible causes (hypotheses, not yet confirmed):**\n" + "\n".join(f"{i + 1}. {h.statement}" for i, h in enumerate(plan.hypotheses)))
        if plan.next_action:
            na = plan.next_action
            lines.append(f"**Most useful next check:** {na.action}" + (f" Expect {na.expected}." if na.expected else "") + f"\n\n_Why this first:_ {na.reason}")
        info = self._result(results, "get_component_info")
        cited: list[str] = []
        if info and info.output.citations:
            top = info.output.citations[0]
            cited = [c.id for c in info.output.citations]
            lines.append(f"Reference note — _{top.title}_{' (illustrative)' if top.illustrative and 'illustrative' not in top.title.lower() else ''}, “{top.section}”: {self._first_sentences(top.excerpt, 2)}")
        lines.append("Tell me what you measure (e.g. “I measured 4.2 V at VOUT”) and I'll compare it with the expected value.")
        return FinalAnswer(
            answer="\n\n".join(lines),
            confirmed_findings=plan.confirmed,
            hypotheses=plan.hypotheses,
            next_step=plan.next_action,
            cited_passage_ids=cited,
        )

    def _compose_calculate(self, state: AgentState, intent: Intent, results: list[ToolResultRecord]) -> FinalAnswer:
        r = self._result(results, "calculate_electrical_values")
        if r is None or r.output.status != "ok":
            return FinalAnswer(answer=f"The calculation failed: {r.output.data.get('error') if r else 'no result'}")
        calc = r.output.calculations[0]
        lines = [f"**{CALC_TITLES.get(calc.calculation, calc.calculation)}** — {calc.formula}"]
        lines.append("Inputs (from the verified netlist" + (" and stated assumptions" if calc.assumptions else "") + "): " + ", ".join(f"{k} = {v.display}" for k, v in calc.inputs.items()))
        lines.append("\n".join(f"- {s}" for s in calc.steps))
        lines.append("**Result:** " + ", ".join(f"{k.replace('_', ' ')} = **{v.display}**" for k, v in calc.outputs.items()))
        if calc.assumptions:
            lines.append("_Assumptions:_ " + " ".join(calc.assumptions))
        if calc.warnings:
            lines.append("⚠️ " + " ".join(calc.warnings))
        main_key = next(iter(calc.outputs))
        return FinalAnswer(
            answer="\n\n".join(lines),
            confirmed_findings=[Finding(statement=f"{main_key.replace('_', ' ')} = {calc.outputs[main_key].display}", basis="calculation", detail=calc.formula)],
            follow_up_questions=["How can I verify this with a multimeter?"],
        )

    def _compose_calc_missing(self, state: AgentState, intent: Intent, results: list[ToolResultRecord]) -> FinalAnswer:
        note = "" if state.insight else self._no_connectivity_note(state) + " "
        return FinalAnswer(
            answer=note + "I don't have a topology-specific calculation for this circuit. Give me the values explicitly (for example “5 V across 1 kΩ”) and I'll compute it.",
            follow_up_questions=["Which voltage and resistance should I use?"],
        )

    def _compose_datasheet(self, state: AgentState, intent: Intent, results: list[ToolResultRecord]) -> FinalAnswer:
        r = self._result(results, "get_component_info")
        if r is None or r.output.status != "ok":
            return FinalAnswer(answer=r.output.data.get("error", "Lookup failed.") if r else "Lookup failed.")
        if not r.output.citations:
            return FinalAnswer(answer="I found no matching passage in the local datasheet collection, so I can't quote a specification. Please check the manufacturer's datasheet for your exact part.")
        lines = ["Relevant passages from the local datasheet collection:"]
        for c in r.output.citations:
            page = f", page {c.page}" if c.page else ""
            label = " — illustrative teaching data, not a manufacturer spec" if c.illustrative else ""
            lines.append(f"- _{c.title}_ ({c.source}{page}), “{c.section}”{label}: {self._first_sentences(c.excerpt, 3)}")
        return FinalAnswer(
            answer="\n".join(lines),
            confirmed_findings=[Finding(statement=f"Retrieved: {r.output.citations[0].section} — {r.output.citations[0].title}", basis="datasheet")],
            cited_passage_ids=[c.id for c in r.output.citations],
        )

    def _compose_explain(self, state: AgentState, intent: Intent, results: list[ToolResultRecord]) -> FinalAnswer:
        a = state.session.analysis
        ins = state.insight
        if ins is None:
            if a.components:
                comps = ", ".join(f"{c.ref} ({c.type}{', ' + c.value if c.value else ''})" for c in a.components)
                return FinalAnswer(answer=f"{a.summary}\n\nDetected components: {comps}.\n\n{self._no_connectivity_note(state)}")
            return FinalAnswer(answer=self._no_connectivity_note(state), follow_up_questions=["Load the LED sample to see a full analysis."])
        lines = [f"**{ins.title}.** {ins.summary}"]
        if ins.expected_behavior:
            lines.append(f"**Expected behaviour:** {ins.expected_behavior}")
        lines.append("**Component roles** (derived from the verified netlist):\n" + "\n".join(f"- **{ref}** — {role}" for ref, role in ins.roles.items()))
        confirmed = []
        r = self._result(results, "calculate_electrical_values")
        if r and r.output.status == "ok":
            calc = r.output.calculations[0]
            main = next(iter(calc.outputs))
            confirmed.append(Finding(statement=f"{main.replace('_', ' ')} = {calc.outputs[main].display}", basis="calculation", detail=calc.formula))
        return FinalAnswer(
            answer="\n\n".join(lines),
            confirmed_findings=confirmed,
            follow_up_questions=["What should I measure first?", "What could go wrong in this circuit?"],
        )

    def _compose_fallback(self, state: AgentState, intent: Intent, results: list[ToolResultRecord]) -> FinalAnswer:
        if intent.calls:
            return self._compose_explain(state, intent, results)
        comps = [c.ref for c in state.session.analysis.components[:3]]
        return FinalAnswer(
            answer="I'm not sure what you'd like to know. In demo mode I can explain the circuit, describe a component's role, trace connections, run calculations, search the datasheet notes, and plan measurements.",
            follow_up_questions=["Explain this circuit."] + [f"What is the purpose of {c}?" for c in comps[:2]] + ["What should I measure next?"],
        )

    @staticmethod
    def _first_sentences(text: str, n: int = 2) -> str:
        flat = " ".join(line.strip("-> ").strip() for line in text.splitlines() if line.strip())
        parts = re.split(r"(?<=[.!?])\s+", flat)
        return " ".join(parts[:n])[:400]
