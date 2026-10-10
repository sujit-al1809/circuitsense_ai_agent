---
title: Generic 5 mm red indicator LED (illustrative reference)
component_types: led
part_numbers: GENERIC-LED-RED-5MM
illustrative: true
---

> ILLUSTRATIVE TEACHING DATA — not a manufacturer datasheet. The figures below are rounded,
> typical values for a generic 5 mm red indicator LED, provided so CircuitSense can demonstrate
> datasheet retrieval. Always check the datasheet of the exact LED you are using.

## Overview

A light-emitting diode (LED) emits light when forward current flows from anode to cathode.
Like any diode it has a roughly constant forward voltage once it conducts, so it must be driven
by a current-limiting element (usually a series resistor) rather than directly from a voltage source.

## Electrical characteristics (typical, illustrative)

- Forward voltage (Vf) at 20 mA: 2.0 V typical, 1.8 V to 2.2 V range.
- Maximum continuous forward current: 20 mA.
- Recommended operating current for indicators: 5 mA to 15 mA.
- Maximum reverse voltage: 5 V. LEDs are not designed to block large reverse voltages.

## Pin identification

The anode is the longer lead; the cathode is the shorter lead and sits next to the flat side of the
package rim. In the KiCad Device:LED symbol, pin 1 is the cathode (K) and pin 2 is the anode (A).

## Choosing a series resistor

R = (Vsupply − Vf) / I. For a 5 V supply, Vf = 2.0 V and I = 10 mA, R = 300 Ω; the nearest standard
value above is 330 Ω, giving about 9.1 mA. Resistor power is I² × R, about 27 mW in that case.

## Troubleshooting

- LED dark, about 0 V across it: no current — check the supply, an open resistor, or a broken joint.
- LED dark, close to the full supply across it: the LED is open or inserted backwards.
- LED dim: current too low — resistor value too high, supply sagging, or an LED with a higher Vf (blue/white).
- Multimeter diode test (power removed): many red LEDs glow faintly and read about 1.6 V to 1.9 V forward.
