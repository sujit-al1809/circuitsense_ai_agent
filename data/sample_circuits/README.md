# Reference circuits

Each folder contains:

| File | What it is |
| --- | --- |
| `<id>.net` | **Hand-authored reference netlist in KiCad netlist (S-expression) format.** It was written by hand for CircuitSense, not exported from Eeschema; the `tool` field in the file says so. It is the ground truth for connectivity. |
| `schematic.png` | Schematic drawn by `backend/scripts/generate_sample_assets.py`. |
| `schematic.pdf` | (LED sample only) The same drawing embedded in an A4 PDF, for testing the PDF upload path. |
| `annotations.json` | Exact, normalised (0–1) component bounding boxes recorded by the generator while drawing (`components` for the PNG, `pdf.components` for the PDF page). Labelled `sample_annotation` in the API. |
| `circuit.json` | Name, description, expected behaviour, example questions, and test questions with expected answer fragments and expected tools. |

## Validation

`backend/tests/test_circuit_core.py` checks the following:

- Each netlist's connectivity matches an independently written table of expected nets.
- The annotated components match the netlist components exactly, and every bounding box is valid.
- The DC estimate gives the values below.
- The topology classifier identifies each circuit and assigns the expected roles.

`backend/tests/test_agent.py` runs every `test_questions` entry through the demo agent and asserts the expected text and tools.

## Circuits

### `led_basic`: LED indicator
V1 = 5 V → R1 = 330 Ω → D1 (red LED, anode on `LED_A`, cathode on `GND`).

- I = (5 V − 2.0 V) / 330 Ω ≈ **9.09 mA**. Vf = 2.0 V is an *assumption* from the illustrative LED reference; the agent tells users to measure the real Vf.
- V(R1) ≈ 3 V, P(R1) ≈ 27.3 mW, P(D1) ≈ 18.2 mW.
- Node voltages: VCC = 5 V, LED_A ≈ 2.0 V, GND = 0 V.

### `resistor_divider`: 12 V → 3.84 V
V1 = 12 V on `VIN`, R1 = 10 kΩ from `VIN` to `VOUT`, R2 = 4.7 kΩ from `VOUT` to `GND`, J1 brings `VOUT`/`GND` to a header.

- VOUT = 12 × 4.7 / 14.7 ≈ **3.84 V** (unloaded); divider current ≈ 816 µA.
- A load on J1 appears in parallel with R2 and lowers VOUT. This is the first hypothesis for "low output voltage".

### `noninverting_amp`: gain 11
U1 is a generic op-amp using the standard single op-amp 8-pin pinout (2 = IN−, 3 = IN+, 4 = V−, 6 = OUT, 7 = V+). V2 provides +12 V on `VCC`; V3 provides −12 V on `VEE` (its + terminal is on `GND`). V1 supplies 0.1 V on `VIN`. Rg = R1 = 1 kΩ (`FB` → `GND`), Rf = R2 = 10 kΩ (`VOUT` → `FB`), load R3 = 10 kΩ.

- Gain = 1 + 10k / 1k = **11** (20.8 dB), VOUT ≈ **1.1 V**, FB ≈ VIN = 0.1 V.
- Verified feedback path: `U1.6 (OUT) → net VOUT → R2 → net FB → U1.2 (IN-)`.
