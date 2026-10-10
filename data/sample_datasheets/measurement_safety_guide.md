---
title: CircuitSense measurement and safety guide
component_types: general
part_numbers:
illustrative: false
---

> General measurement practice written for CircuitSense. Advisory only.

## Before measuring

- Know the highest voltage present. Above about 50 V DC or in any mains-connected circuit, use CAT-rated
  equipment, isolate power and get qualified supervision. Never work on live mains circuits as a beginner.
- Large capacitors store energy after power is removed; discharge them safely and verify with a meter.
- High-current supplies and batteries can deliver dangerous fault currents even at low voltage; avoid shorting probes.

## Voltage measurements

Measure node voltages with the black probe on ground (GND) and the red probe on the node. To measure the
voltage across a component, place the probes on its two terminals. Voltage is measured with the circuit powered.

## Current measurements

Current is measured in series with the circuit, which requires breaking the path. A safer alternative is to
measure the voltage across a known resistor and compute I = V / R.

## Resistance and diode tests

Always remove power first. In-circuit resistance readings are affected by parallel paths. Use the diode-test
mode to check diodes and LEDs: forward drop in one direction, OL in the other.
