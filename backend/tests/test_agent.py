"""Agent orchestration: iteration limits, tool failures, guardrails, demo answers."""

from __future__ import annotations

import json

import pytest

from tests.conftest import SAMPLES, make_settings, png_bytes
from app.schemas.chat import Finding
from app.services.agent import AgentOrchestrator, FinalAnswer, ModelTurn, ToolCall
from app.services.container import build_services
from app.services.storage import Session


class ScriptedPolicy:
    """Returns pre-defined turns; records the states it saw."""

    mode = "bedrock"

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = 0

    def next_turn(self, state):
        self.calls += 1
        turn = self.turns.pop(0) if self.turns else self.turns_default(state)
        return turn(state) if callable(turn) else turn

    def turns_default(self, state):
        return ModelTurn(final=FinalAnswer(answer="done"))


class AlwaysToolPolicy:
    mode = "bedrock"

    def __init__(self):
        self.calls = 0

    def next_turn(self, state):
        self.calls += 1
        return ModelTurn(tool_calls=[ToolCall(id=f"t{self.calls}", name="trace_net", input={"target": "R1"})])


def _orchestrator(services, policy, **overrides):
    settings = make_settings(**overrides)
    return AgentOrchestrator(settings, policy, services.retrieval, services.simulation, services.measurements)


def _led_session(services) -> Session:
    return services.analysis.load_sample("led_basic")


def test_iteration_limit_is_enforced(services):
    policy = AlwaysToolPolicy()
    orch = _orchestrator(services, policy, agent_max_iterations=3)
    r = orch.run(_led_session(services), "trace forever", None)
    assert policy.calls == 3
    assert r.stopped_reason == "max_iterations"
    assert r.iterations == 3
    assert "limit of 3" in r.answer
    assert any("maximum of 3" in w for w in r.warnings)
    assert len(r.tool_calls) == 3


def test_failing_tool_is_not_retried_and_stops(services):
    bad = ToolCall(id="x", name="calculate_electrical_values", input={"calculation": "ohms_law", "params": {"voltage": 5}})
    policy = ScriptedPolicy([ModelTurn(tool_calls=[bad]), ModelTurn(tool_calls=[bad]), ModelTurn(tool_calls=[bad]), ModelTurn(tool_calls=[bad])])
    orch = _orchestrator(services, policy, agent_max_iterations=10, agent_max_tool_failures=3)
    r = orch.run(_led_session(services), "calc", None)
    assert r.stopped_reason == "tool_failures"
    assert policy.calls == 3
    assert [t.status for t in r.tool_calls] == ["error", "error", "error"]
    assert "not retried" in r.tool_calls[1].summary
    assert "failed" in r.answer.lower()


def test_per_iteration_tool_budget(services):
    calls = [ToolCall(id=str(i), name="trace_net", input={"target": "R1"}) for i in range(6)]
    policy = ScriptedPolicy([ModelTurn(tool_calls=calls)])
    orch = _orchestrator(services, policy, agent_max_tool_calls_per_iteration=2, agent_max_tool_failures=10)
    r = orch.run(_led_session(services), "q", None)
    assert [t.status for t in r.tool_calls].count("ok") == 2
    assert all("budget" in t.summary for t in r.tool_calls[2:])


def test_unknown_tool_and_invalid_input(services):
    policy = ScriptedPolicy([ModelTurn(tool_calls=[ToolCall(id="a", name="rm_rf", input={}), ToolCall(id="b", name="trace_net", input={"target": ""})])])
    r = _orchestrator(services, policy, agent_max_tool_failures=5).run(_led_session(services), "q", None)
    assert r.tool_calls[0].status == "error" and "unknown tool" in r.tool_calls[0].summary
    assert r.tool_calls[1].status == "error" and "invalid input" in r.tool_calls[1].summary


def test_findings_are_downgraded_without_evidence(services):
    session = services.analysis.analyze_upload(
        __import__("app.services.analysis_service", fromlist=["UploadedFile"]).UploadedFile("x.png", png_bytes())
    )
    final = FinalAnswer(
        answer="ok",
        confirmed_findings=[
            Finding(statement="R1 connects to D1", basis="parsed_netlist"),
            Finding(statement="Vout simulated at 3 V", basis="simulation"),
            Finding(statement="User measured 4 V", basis="user_measurement"),
            Finding(statement="Seen in image", basis="image_inference"),
        ],
        cited_passage_ids=["ds-fabricated"],
    )
    r = _orchestrator(services, ScriptedPolicy([ModelTurn(final=final)])).run(session, "q", None)
    assert r.confirmed == []
    assert len(r.hypotheses) == 4
    assert r.citations == []
    assert any("never retrieved" in w for w in r.warnings)


def test_citations_only_from_retrieved_passages(services):
    def second(state):
        real_id = state.steps[0].results[0].output.citations[0].id
        return ModelTurn(final=FinalAnswer(answer="see datasheet", cited_passage_ids=[real_id, "ds-made-up"],
                                           confirmed_findings=[Finding(statement="Max 20 mA", basis="datasheet")]))

    policy = ScriptedPolicy([ModelTurn(tool_calls=[ToolCall(id="1", name="get_component_info", input={"ref": "D1", "query": "maximum forward current"})]), second])
    r = _orchestrator(services, policy).run(_led_session(services), "max current?", None)
    assert r.citations and r.citations[0].source == "generic_red_led.md"
    assert all(c.id != "ds-made-up" for c in r.citations)
    assert r.confirmed[0].basis == "datasheet"


def test_record_measurement_refuses_fabricated_values(services):
    session = _led_session(services)
    call = ToolCall(id="m", name="record_measurement", input={"quantity": "voltage", "value": 3.3, "unit": "V", "target": "VCC"})
    r = _orchestrator(services, ScriptedPolicy([ModelTurn(tool_calls=[call])])).run(session, "the LED is dim", None)
    assert r.tool_calls[0].status == "error"
    assert session.measurements == []
    ok_session = _led_session(services)
    r2 = _orchestrator(services, ScriptedPolicy([ModelTurn(tool_calls=[call])])).run(ok_session, "I read 3.3 V on VCC", None)
    assert r2.tool_calls[0].status == "ok"
    assert len(ok_session.measurements) == 1


def test_provider_error_produces_clear_answer(services):
    from app.services.bedrock_service import ProviderError

    class Failing:
        mode = "bedrock"

        def next_turn(self, state):
            raise ProviderError("Access denied by Amazon Bedrock.", code="AccessDeniedException")

    r = _orchestrator(services, Failing()).run(_led_session(services), "q", None)
    assert r.stopped_reason == "provider_error"
    assert "provider returned an error" in r.answer
    assert r.next_step is not None  # deterministic planner still offers a next check


# --------------------------------------------------------------------------- demo agent on sample test questions


def _sample_questions():
    for meta_path in sorted(SAMPLES.glob("*/circuit.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        for q in meta["test_questions"]:
            yield pytest.param(meta["id"], q, id=f"{meta['id']}:{q['question']}")


@pytest.mark.parametrize("sample_id,case", list(_sample_questions()))
def test_demo_agent_answers_sample_questions(sample_id, case):
    services = build_services(make_settings())
    session = services.analysis.load_sample(sample_id)
    r = services.orchestrator.run(session, case["question"], None)
    assert r.mode == "demo"
    assert r.stopped_reason == "final"
    text = r.answer + " ".join(f.statement for f in r.confirmed) + (r.next_step.action if r.next_step else "")
    for expected in case["expected_contains"]:
        assert expected in text, f"{expected!r} missing from: {text}"
    used = {t.name for t in r.tool_calls}
    assert set(case["expected_tools"]) <= used
    assert "no AI model was called" in r.answer


def test_demo_focus_component_context():
    services = build_services(make_settings())
    session = services.analysis.load_sample("led_basic")
    r = services.orchestrator.run(session, "Why is this resistor needed?", "R1")
    assert "current-limiting" in r.answer
    assert {t.name for t in r.tool_calls} == {"get_component_info", "trace_net"}
    assert r.citations


def test_demo_measurement_flow_updates_plan():
    services = build_services(make_settings())
    session = services.analysis.load_sample("resistor_divider")
    r = services.orchestrator.run(session, "I measured 12 V at VIN", None)
    assert r.measurements_recorded and r.measurements_recorded[0].target == "VIN"
    assert r.next_step.target == "VOUT"
    r2 = services.orchestrator.run(session, "Now I measured 3.0 V at VOUT", None)
    assert "outside" in r2.answer
    assert r2.next_step.target == "R2"


def test_demo_without_connectivity_is_explicit():
    from app.services.analysis_service import UploadedFile

    services = build_services(make_settings())
    session = services.analysis.analyze_upload(UploadedFile("x.png", png_bytes()))
    r = services.orchestrator.run(session, "Why might my output voltage be low?", None)
    assert "no verified connectivity" in r.answer.lower()
    assert r.confirmed == []


def test_demo_unknown_component_reference():
    services = build_services(make_settings())
    r = services.orchestrator.run(services.analysis.load_sample("led_basic"), "Find the role of R3", None)
    assert "R3 is not part of this circuit" in r.answer


def test_demo_simulation_unavailable_is_labelled():
    services = build_services(make_settings())
    r = services.orchestrator.run(services.analysis.load_sample("led_basic"), "Simulate this circuit", None)
    assert r.tool_calls[0].status == "unavailable"
    assert "not* an ngspice simulation" in r.answer
    assert all(f.basis != "simulation" for f in r.confirmed)
