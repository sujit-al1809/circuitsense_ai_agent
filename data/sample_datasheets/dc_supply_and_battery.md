---
title: DC supplies and batteries (illustrative reference)
component_types: voltage_source, battery
part_numbers: GENERIC-DC-SOURCE
illustrative: true
---

> ILLUSTRATIVE TEACHING DATA — general guidance, not a datasheet for a specific supply or cell.

## Ideal vs. real sources

An ideal voltage source holds its voltage regardless of load. Real sources have internal resistance:
the terminal voltage sags as current increases. Bench supplies may also enter current-limit (CC) mode,
where the voltage collapses to whatever the load allows.

## Checking a supply

- Measure the voltage at the circuit's supply pins, not just at the source terminals; wiring and connectors drop voltage.
- Compare loaded and unloaded (open-circuit) voltage: a large difference indicates a weak source or an excessive load.
- On a bench supply, check whether the CC (current limit) indicator is lit.

## Batteries

A "9 V" battery reads above 9 V when fresh and drops under load as it discharges. A battery that reads
correctly open-circuit but sags heavily under load is depleted or has high internal resistance.
