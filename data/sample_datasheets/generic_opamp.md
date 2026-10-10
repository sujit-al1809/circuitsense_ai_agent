---
title: Generic general-purpose operational amplifier (illustrative reference)
component_types: opamp, ic
part_numbers: GENERIC-OPAMP-8PIN
illustrative: true
---

> ILLUSTRATIVE TEACHING DATA — not a manufacturer datasheet. Rounded, typical values for a generic
> general-purpose (not rail-to-rail) op-amp. Pinouts and limits differ between parts; always check the
> datasheet of your exact op-amp.

## Pinout (common single op-amp, 8-pin package)

Pin 2: inverting input (IN−). Pin 3: non-inverting input (IN+). Pin 4: negative supply (V−).
Pin 6: output. Pin 7: positive supply (V+). Pins 1, 5 and 8 are commonly offset-null or not connected.

## Operating limits (typical, illustrative)

- Supply: ±5 V to ±15 V (10 V to 30 V total).
- Output swing: typically within about 1.5 V of each supply rail into 10 kΩ; it cannot reach the rails.
- Input common-mode range: typically within about 2 V of each rail.
- Gain-bandwidth product: about 1 MHz.
- Input offset voltage: a few millivolts.

## Negative feedback basics

With negative feedback the op-amp drives its output so that IN− equals IN+ ("virtual short").
Non-inverting gain: G = 1 + Rf / Rg, where Rf connects the output to IN− and Rg connects IN− to ground.
Inverting gain: G = −Rf / Rin.

## Troubleshooting

- Output stuck near a rail: missing feedback (open Rf), missing supply rail, or input outside the common-mode range.
- IN− not equal to IN+: the loop is not regulating — the op-amp is saturated or the feedback path is broken.
- Output lower than expected: wrong Rf/Rg values, heavy load, or output clipping near the rail.
- Measure both supply pins directly at the IC pins, relative to ground, before anything else.
