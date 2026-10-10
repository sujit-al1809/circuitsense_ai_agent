"""Deterministic electrical calculations.

Every function validates its inputs, states its assumptions and returns a
``CalculationResult``. The language model never does the arithmetic itself.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from app.schemas.circuit import CalculationResult, QuantityValue
from app.services.units import UnitError, format_quantity, parse_quantity


class CalculationError(ValueError):
    pass


def _q(value: float, unit: str) -> QuantityValue:
    return QuantityValue(value=value, unit=unit, display=format_quantity(value, unit))


def _num(params: dict[str, Any], key: str, unit: str, *, positive: bool = False, required: bool = True) -> float | None:
    raw = params.get(key)
    if raw is None or raw == "":
        if required:
            raise CalculationError(f"missing required input '{key}' ({unit})")
        return None
    try:
        value = parse_quantity(raw, expected_unit=unit)
    except UnitError as exc:
        raise CalculationError(f"invalid '{key}': {exc}") from exc
    if not math.isfinite(value):
        raise CalculationError(f"'{key}' must be finite")
    if positive and value <= 0:
        raise CalculationError(f"'{key}' must be greater than zero")
    return value


def ohms_law(params: dict[str, Any]) -> CalculationResult:
    v = _num(params, "voltage", "V", required=False)
    i = _num(params, "current", "A", required=False)
    r = _num(params, "resistance", "Ω", positive=True, required=False)
    provided = [x is not None for x in (v, i, r)]
    if sum(provided) != 2:
        raise CalculationError("Ohm's law needs exactly two of: voltage, current, resistance")
    inputs: dict[str, QuantityValue] = {}
    outputs: dict[str, QuantityValue] = {}
    if v is None:
        assert i is not None and r is not None
        v = i * r
        inputs = {"current": _q(i, "A"), "resistance": _q(r, "Ω")}
        outputs = {"voltage": _q(v, "V")}
        formula = "V = I × R"
        steps = [f"V = {format_quantity(i, 'A')} × {format_quantity(r, 'Ω')} = {format_quantity(v, 'V')}"]
    elif i is None:
        assert r is not None
        i = v / r
        inputs = {"voltage": _q(v, "V"), "resistance": _q(r, "Ω")}
        outputs = {"current": _q(i, "A")}
        formula = "I = V / R"
        steps = [f"I = {format_quantity(v, 'V')} / {format_quantity(r, 'Ω')} = {format_quantity(i, 'A')}"]
    else:
        if i == 0:
            raise CalculationError("current must be non-zero to compute resistance")
        r = v / i
        if r <= 0:
            raise CalculationError("voltage and current must have the same sign to compute a resistance")
        inputs = {"voltage": _q(v, "V"), "current": _q(i, "A")}
        outputs = {"resistance": _q(r, "Ω")}
        formula = "R = V / I"
        steps = [f"R = {format_quantity(v, 'V')} / {format_quantity(i, 'A')} = {format_quantity(r, 'Ω')}"]
    p = abs(v * i)
    outputs["power"] = _q(p, "W")
    steps.append(f"P = |V × I| = {format_quantity(p, 'W')}")
    return CalculationResult(
        calculation="ohms_law",
        formula=formula,
        inputs=inputs,
        outputs=outputs,
        assumptions=["Linear (ohmic) element at DC steady state."],
        steps=steps,
    )


def voltage_divider(params: dict[str, Any]) -> CalculationResult:
    vin = _num(params, "vin", "V")
    r_top = _num(params, "r_top", "Ω", positive=True)
    r_bottom = _num(params, "r_bottom", "Ω", positive=True)
    r_load = _num(params, "r_load", "Ω", positive=True, required=False)
    assert vin is not None and r_top is not None and r_bottom is not None
    inputs = {"vin": _q(vin, "V"), "r_top": _q(r_top, "Ω"), "r_bottom": _q(r_bottom, "Ω")}
    steps = []
    r_eff = r_bottom
    assumptions = ["Ideal resistors at their marked values (tolerance ignored).", "DC steady state, ideal source."]
    if r_load is not None:
        inputs["r_load"] = _q(r_load, "Ω")
        r_eff = r_bottom * r_load / (r_bottom + r_load)
        steps.append(f"R_bottom ∥ R_load = {format_quantity(r_eff, 'Ω')}")
    else:
        assumptions.append("No load connected to the output (unloaded divider).")
    vout = vin * r_eff / (r_top + r_eff)
    current = vin / (r_top + r_eff)
    steps.append(
        f"Vout = {format_quantity(vin, 'V')} × {format_quantity(r_eff, 'Ω')} / "
        f"({format_quantity(r_top, 'Ω')} + {format_quantity(r_eff, 'Ω')}) = {format_quantity(vout, 'V')}"
    )
    steps.append(f"I(R_top) = Vin / (R_top + R_eff) = {format_quantity(current, 'A')}")
    outputs = {
        "vout": _q(vout, "V"),
        "divider_current": _q(current, "A"),
        "ratio": QuantityValue(value=vout / vin if vin else 0.0, unit="", display=f"{(vout / vin if vin else 0):.4f}"),
    }
    if r_load is not None:
        unloaded = vin * r_bottom / (r_top + r_bottom)
        outputs["vout_unloaded"] = _q(unloaded, "V")
    return CalculationResult(
        calculation="voltage_divider",
        formula="Vout = Vin × R_bottom / (R_top + R_bottom)" + (" with R_bottom ∥ R_load" if r_load else ""),
        inputs=inputs,
        outputs=outputs,
        assumptions=assumptions,
        steps=steps,
    )


def led_series_resistor(params: dict[str, Any]) -> CalculationResult:
    vs = _num(params, "supply_voltage", "V", positive=True)
    vf = _num(params, "forward_voltage", "V", positive=True)
    r = _num(params, "resistance", "Ω", positive=True)
    i_max = _num(params, "max_current", "A", positive=True, required=False)
    assert vs is not None and vf is not None and r is not None
    if vf >= vs:
        raise CalculationError(
            f"forward voltage ({format_quantity(vf, 'V')}) must be below the supply ({format_quantity(vs, 'V')}); "
            "the LED would not conduct meaningfully"
        )
    i = (vs - vf) / r
    p_r = i * i * r
    p_led = vf * i
    warnings = []
    if i_max is not None and i > i_max:
        warnings.append(
            f"Calculated current {format_quantity(i, 'A')} exceeds the stated maximum {format_quantity(i_max, 'A')}."
        )
    inputs = {"supply_voltage": _q(vs, "V"), "forward_voltage": _q(vf, "V"), "resistance": _q(r, "Ω")}
    if i_max is not None:
        inputs["max_current"] = _q(i_max, "A")
    return CalculationResult(
        calculation="led_series_resistor",
        formula="I = (Vs − Vf) / R",
        inputs=inputs,
        outputs={
            "current": _q(i, "A"),
            "resistor_voltage": _q(vs - vf, "V"),
            "resistor_power": _q(p_r, "W"),
            "led_power": _q(p_led, "W"),
        },
        assumptions=[
            "LED forward voltage treated as constant at the stated Vf (real Vf varies with current, colour and temperature).",
            "Ideal supply with no internal resistance.",
        ],
        steps=[
            f"V_R = {format_quantity(vs, 'V')} − {format_quantity(vf, 'V')} = {format_quantity(vs - vf, 'V')}",
            f"I = {format_quantity(vs - vf, 'V')} / {format_quantity(r, 'Ω')} = {format_quantity(i, 'A')}",
            f"P_R = I² × R = {format_quantity(p_r, 'W')}",
            f"P_LED = Vf × I = {format_quantity(p_led, 'W')}",
        ],
        warnings=warnings,
    )


def _resistor_list(params: dict[str, Any]) -> list[float]:
    values = params.get("resistances")
    if not isinstance(values, list) or len(values) < 2:
        raise CalculationError("'resistances' must be a list of at least two values")
    if len(values) > 50:
        raise CalculationError("too many resistances (max 50)")
    out = []
    for idx, raw in enumerate(values):
        try:
            v = parse_quantity(raw, expected_unit="Ω")
        except UnitError as exc:
            raise CalculationError(f"invalid resistance #{idx + 1}: {exc}") from exc
        if v <= 0:
            raise CalculationError(f"resistance #{idx + 1} must be greater than zero")
        out.append(v)
    return out


def series_resistance(params: dict[str, Any]) -> CalculationResult:
    values = _resistor_list(params)
    total = sum(values)
    return CalculationResult(
        calculation="series_resistance",
        formula="R_total = R1 + R2 + …",
        inputs={f"r{i + 1}": _q(v, "Ω") for i, v in enumerate(values)},
        outputs={"total": _q(total, "Ω")},
        steps=[" + ".join(format_quantity(v, "Ω") for v in values) + f" = {format_quantity(total, 'Ω')}"],
    )


def parallel_resistance(params: dict[str, Any]) -> CalculationResult:
    values = _resistor_list(params)
    total = 1.0 / sum(1.0 / v for v in values)
    return CalculationResult(
        calculation="parallel_resistance",
        formula="1 / R_total = 1/R1 + 1/R2 + …",
        inputs={f"r{i + 1}": _q(v, "Ω") for i, v in enumerate(values)},
        outputs={"total": _q(total, "Ω")},
        steps=[f"R_total = {format_quantity(total, 'Ω')}"],
    )


def _gain_db(gain: float) -> float:
    return 20 * math.log10(abs(gain))


def non_inverting_gain(params: dict[str, Any]) -> CalculationResult:
    rf = _num(params, "rf", "Ω", positive=True)
    rg = _num(params, "rg", "Ω", positive=True)
    vin = _num(params, "vin", "V", required=False)
    assert rf is not None and rg is not None
    gain = 1 + rf / rg
    inputs = {"rf": _q(rf, "Ω"), "rg": _q(rg, "Ω")}
    outputs = {
        "gain": QuantityValue(value=gain, unit="V/V", display=f"{gain:.4g} V/V"),
        "gain_db": QuantityValue(value=_gain_db(gain), unit="dB", display=f"{_gain_db(gain):.1f} dB"),
    }
    steps = [f"G = 1 + {format_quantity(rf, 'Ω')} / {format_quantity(rg, 'Ω')} = {gain:.4g}"]
    if vin is not None:
        inputs["vin"] = _q(vin, "V")
        outputs["vout"] = _q(gain * vin, "V")
        steps.append(f"Vout = G × Vin = {format_quantity(gain * vin, 'V')}")
    return CalculationResult(
        calculation="non_inverting_gain",
        formula="G = 1 + Rf / Rg",
        inputs=inputs,
        outputs=outputs,
        assumptions=[
            "Ideal op-amp: very high open-loop gain, negligible input bias current.",
            "Output stays within the op-amp's output swing limits (not checked here).",
        ],
        steps=steps,
    )


def inverting_gain(params: dict[str, Any]) -> CalculationResult:
    rf = _num(params, "rf", "Ω", positive=True)
    rin = _num(params, "rin", "Ω", positive=True)
    vin = _num(params, "vin", "V", required=False)
    assert rf is not None and rin is not None
    gain = -rf / rin
    inputs = {"rf": _q(rf, "Ω"), "rin": _q(rin, "Ω")}
    outputs = {
        "gain": QuantityValue(value=gain, unit="V/V", display=f"{gain:.4g} V/V"),
        "gain_db": QuantityValue(value=_gain_db(gain), unit="dB", display=f"{_gain_db(gain):.1f} dB"),
    }
    steps = [f"G = −{format_quantity(rf, 'Ω')} / {format_quantity(rin, 'Ω')} = {gain:.4g}"]
    if vin is not None:
        inputs["vin"] = _q(vin, "V")
        outputs["vout"] = _q(gain * vin, "V")
        steps.append(f"Vout = G × Vin = {format_quantity(gain * vin, 'V')}")
    return CalculationResult(
        calculation="inverting_gain",
        formula="G = −Rf / Rin",
        inputs=inputs,
        outputs=outputs,
        assumptions=["Ideal op-amp with the non-inverting input at the reference (ground)."],
        steps=steps,
    )


def rc_time_constant(params: dict[str, Any]) -> CalculationResult:
    r = _num(params, "resistance", "Ω", positive=True)
    c = _num(params, "capacitance", "F", positive=True)
    assert r is not None and c is not None
    tau = r * c
    fc = 1 / (2 * math.pi * tau)
    return CalculationResult(
        calculation="rc_time_constant",
        formula="τ = R × C, f_c = 1 / (2πRC)",
        inputs={"resistance": _q(r, "Ω"), "capacitance": _q(c, "F")},
        outputs={"tau": _q(tau, "s"), "cutoff_frequency": _q(fc, "Hz")},
        assumptions=["First-order RC network driven by an ideal source."],
        steps=[f"τ = {format_quantity(tau, 's')}", f"f_c = {format_quantity(fc, 'Hz')}"],
    )


CALCULATIONS: dict[str, Callable[[dict[str, Any]], CalculationResult]] = {
    "ohms_law": ohms_law,
    "voltage_divider": voltage_divider,
    "led_series_resistor": led_series_resistor,
    "series_resistance": series_resistance,
    "parallel_resistance": parallel_resistance,
    "non_inverting_gain": non_inverting_gain,
    "inverting_gain": inverting_gain,
    "rc_time_constant": rc_time_constant,
}


def calculate(calculation: str, params: dict[str, Any]) -> CalculationResult:
    fn = CALCULATIONS.get(calculation)
    if fn is None:
        raise CalculationError(
            f"unsupported calculation '{calculation}'. Supported: {', '.join(sorted(CALCULATIONS))}"
        )
    if not isinstance(params, dict):
        raise CalculationError("params must be an object")
    return fn(params)
