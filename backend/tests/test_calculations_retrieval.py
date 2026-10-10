from __future__ import annotations

import math

import pytest

from app.config import REPO_ROOT
from app.services.calculations import CalculationError, calculate
from app.services.retrieval_service import KeywordRetrievalBackend, tokenize
from app.services.units import UnitError, format_quantity, parse_quantity, unit_to_si

# --------------------------------------------------------------------------- units


@pytest.mark.parametrize(
    "text,unit,expected",
    [("330", "Ω", 330), ("4.7k", "Ω", 4700), ("4k7", "Ω", 4700), ("2R2", "Ω", 2.2), ("10kΩ", "Ω", 10_000),
     ("1M", "Ω", 1e6), ("330 ohms", "Ω", 330), ("5V", "V", 5), ("100mV", "V", 0.1), ("100nF", "F", 1e-7), ("9.09 mA", "A", 9.09e-3)],
)
def test_parse_quantity(text, unit, expected):
    assert parse_quantity(text, expected_unit=unit) == pytest.approx(expected)


@pytest.mark.parametrize("text,unit", [("5V", "Ω"), ("abc", "V"), ("", "V"), ("10 parsecs", "Ω")])
def test_parse_quantity_errors(text, unit):
    with pytest.raises(UnitError):
        parse_quantity(text, expected_unit=unit)


def test_unit_to_si_and_format():
    assert unit_to_si(250, "mV", "voltage") == (0.25, "V")
    assert unit_to_si(4.7, "k", "resistance") == (4700, "Ω")
    with pytest.raises(UnitError):
        unit_to_si(1, "V", "current")
    assert format_quantity(0.0090909, "A") == "9.09 mA"
    assert format_quantity(3.8367, "V") == "3.84 V"
    assert format_quantity(330, "Ω") == "330 Ω"


# --------------------------------------------------------------------------- calculations


def test_ohms_law_all_directions():
    assert calculate("ohms_law", {"voltage": "5V", "resistance": "1k"}).outputs["current"].value == pytest.approx(5e-3)
    assert calculate("ohms_law", {"current": 0.002, "resistance": 470}).outputs["voltage"].value == pytest.approx(0.94)
    r = calculate("ohms_law", {"voltage": 3, "current": "9.09mA"})
    assert r.outputs["resistance"].value == pytest.approx(330, rel=1e-3)
    assert r.outputs["power"].value == pytest.approx(3 * 9.09e-3)


@pytest.mark.parametrize(
    "params",
    [{"voltage": 5}, {"voltage": 5, "current": 1, "resistance": 5}, {"voltage": 5, "resistance": 0}, {"voltage": "5Ω", "resistance": 1}],
)
def test_ohms_law_validation(params):
    with pytest.raises(CalculationError):
        calculate("ohms_law", params)


def test_led_series_resistor_reference_value():
    r = calculate("led_series_resistor", {"supply_voltage": 5, "forward_voltage": 2.0, "resistance": 330})
    assert r.outputs["current"].value == pytest.approx(3 / 330)
    assert r.outputs["current"].display == "9.09 mA"
    assert r.outputs["resistor_power"].value == pytest.approx((3 / 330) ** 2 * 330)
    with pytest.raises(CalculationError, match="forward voltage"):
        calculate("led_series_resistor", {"supply_voltage": 2, "forward_voltage": 2.0, "resistance": 330})
    warn = calculate("led_series_resistor", {"supply_voltage": 5, "forward_voltage": 2.0, "resistance": 100, "max_current": "20mA"})
    assert warn.warnings


def test_divider_with_and_without_load():
    unloaded = calculate("voltage_divider", {"vin": 12, "r_top": "10k", "r_bottom": "4.7k"})
    assert unloaded.outputs["vout"].value == pytest.approx(12 * 4.7 / 14.7)
    loaded = calculate("voltage_divider", {"vin": 12, "r_top": "10k", "r_bottom": "4.7k", "r_load": "4.7k"})
    assert loaded.outputs["vout"].value < unloaded.outputs["vout"].value
    assert loaded.outputs["vout_unloaded"].value == pytest.approx(unloaded.outputs["vout"].value)


def test_amplifier_gains_and_networks():
    ni = calculate("non_inverting_gain", {"rf": "10k", "rg": "1k", "vin": 0.1})
    assert ni.outputs["gain"].value == pytest.approx(11)
    assert ni.outputs["vout"].value == pytest.approx(1.1)
    assert ni.outputs["gain_db"].value == pytest.approx(20 * math.log10(11))
    assert calculate("inverting_gain", {"rf": "100k", "rin": "10k"}).outputs["gain"].value == pytest.approx(-10)
    assert calculate("series_resistance", {"resistances": ["1k", "2.2k", 330]}).outputs["total"].value == pytest.approx(3530)
    assert calculate("parallel_resistance", {"resistances": ["1k", "1k"]}).outputs["total"].value == pytest.approx(500)
    rc = calculate("rc_time_constant", {"resistance": "10k", "capacitance": "100n"})
    assert rc.outputs["tau"].value == pytest.approx(1e-3)
    with pytest.raises(CalculationError, match="unsupported"):
        calculate("fourier_magic", {})


# --------------------------------------------------------------------------- retrieval


@pytest.fixture(scope="module")
def retrieval():
    return KeywordRetrievalBackend(REPO_ROOT / "data" / "sample_datasheets")


def test_tokenize_keeps_part_numbers():
    assert "1n4148" in tokenize("Is the 1N4148 OK?")
    assert "what" not in tokenize("what is it")


def test_retrieval_returns_attributed_led_passage(retrieval):
    hits = retrieval.search("maximum forward current", component_type="led", k=3)
    assert hits
    top = hits[0]
    assert top.source == "generic_red_led.md"
    assert "20 mA" in top.excerpt
    assert top.illustrative is True
    assert top.section and "Electrical characteristics" in top.section
    assert top.page is None  # markdown has no pages


def test_retrieval_pdf_page_numbers(retrieval):
    hits = retrieval.search("diode test reverse OL shorted", component_type="diode", k=5)
    pdf_hits = [h for h in hits if h.source.endswith(".pdf")]
    assert pdf_hits, hits
    assert pdf_hits[0].page == 2
    assert pdf_hits[0].illustrative is True


def test_component_type_boost(retrieval):
    hits = retrieval.search("troubleshooting output", component_type="opamp", k=2)
    assert hits[0].source == "generic_opamp.md"


def test_retrieval_no_match_and_empty(retrieval, tmp_path):
    assert retrieval.search("zzqx flibbertigibbet") == []
    empty = KeywordRetrievalBackend(tmp_path)
    assert empty.document_count == 0 and empty.search("led") == []


def test_retrieval_skips_broken_documents(tmp_path):
    (tmp_path / "ok.md").write_text("---\ntitle: OK doc\n---\n## Part\nResistor tolerance notes", encoding="utf-8")
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-1.7 not really a pdf")
    backend = KeywordRetrievalBackend(tmp_path)
    assert backend.document_count == 1
    assert backend.search("tolerance")[0].title == "OK doc"
