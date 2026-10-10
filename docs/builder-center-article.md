# Building CircuitSense: an evidence-first AI agent for debugging electronic circuits

*Tags: #agents #amazon-bedrock #amazon-nova #generative-ai #electronics*

> **Draft.** The screenshots referenced below were captured from the running app in **demo mode**, which uses a deterministic rule-based policy and labels every answer as such. Replace or add Bedrock-mode screenshots after you run the app with your own Bedrock model access.

## The problem: "looks right" is not good enough for hardware

Ask a general-purpose chatbot to explain a photo of a schematic and you often get a confident, plausible answer. In electronics that is risky. A misread wire, an invented resistor value or a made-up datasheet limit can send a student down the wrong path for an afternoon. For an engineer at a bench, it can damage a board. The hard part of circuit debugging is not producing text. It is knowing **which facts are actually established**: which connections are real, which numbers were computed, which limits come from a datasheet, and which ideas are still hypotheses.

CircuitSense is an agent built around that distinction. It helps students, hardware engineers and PCB designers understand schematics, trace connections, look up component data and decide what to measure next. Every statement carries its evidence.

## What the agent does

You upload a schematic image, a PDF or a KiCad netlist, then ask questions: *"How does this circuit work?"*, *"What is the purpose of R1?"*, *"Why might my output voltage be low?"*, *"What should I measure next?"*.

Behind the chat, a tool-using agent decides which of five tools it needs:

1. **`get_component_info`** returns component details plus datasheet passages retrieved from a local collection, with the file, page and section.
2. **`trace_net`** walks a NetworkX graph of components, pins and nets, and returns *verified* connectivity only.
3. **`calculate_electrical_values`** handles Ohm's law, dividers, LED resistors and amplifier gain, computed in Python, never by the model.
4. **`record_measurement`** stores readings the user reports. It refuses any value that does not appear in the user's message.
5. **`simulate_circuit`** runs ngspice on a deck generated from a verified netlist, with a timeout and no shell. If ngspice is not installed, it says so instead of inventing results.

The model ends each turn by calling a `submit_answer` tool with a structured answer: confirmed findings (each with a *basis*), hypotheses, one recommended next measurement with its reason and expected reading, and the IDs of the datasheet passages it relied on.

## Evidence is part of the data model

The most important design choice sits in the schemas rather than the prompt. Every component and net carries a **provenance**: `parsed_netlist`, `image_inference`, `datasheet`, `calculation`, `user_measurement` or `simulation`. A Pydantic validator rejects any net marked *verified* unless it came from a parsed netlist. Connections a vision model reads from an image are stored as *inferred* and shown with dashed outlines and an "unverified" label.

The orchestrator then enforces a few rules that do not depend on the model behaving well:

- **Bounded loops:** a maximum number of iterations, a per-step tool budget, and a cap on failed tool calls. An identical call that already failed is never retried.
- **Honest findings:** if the model claims something is "confirmed by the netlist" but the circuit has no verified netlist, the finding is moved to *hypotheses* with a warning. The same applies to simulation claims without a simulation, and measurement claims without measurements.
- **Real citations only:** citations are built from passages a tool actually returned. A citation to an ID that was never retrieved is dropped.
- **Untrusted input stays data:** text from uploaded files and from the vision model is wrapped in `<untrusted>` delimiters, and the system prompt says to ignore instructions inside them.

## Amazon Bedrock integration

CircuitSense uses the **Amazon Bedrock Converse API** through boto3 with **Amazon Nova** models in two roles. **Nova Pro** reads schematic images and reports components, values and bounding boxes through a strict tool schema. **Nova 2 Lite** is the agent brain that orchestrates the five tools. Tool schemas are reduced to a plain JSON Schema subset, temperature is set to 0 for reliable tool calls, and any `<thinking>` text the model emits is stripped before answers reach the user. Model IDs and the region come from configuration, never from code. A status endpoint checks that each configured model or inference profile exists and accepts the right input modality, and it can optionally send a tiny probe request. Assistant content, including reasoning blocks, is replayed unchanged between tool steps, and sampling parameters are only sent if you configure them.

For local development and demos without credentials, a **demo mode** drives the same tools with a deterministic rule-based policy. It is labelled in the API (`"mode": "demo"`), in the UI badge and in every answer. It never pretends to be model output.

## The Focus Component interaction

The feature that makes the workspace feel like an engineering tool is **Focus Component**. Click R1 in the component list or directly on the schematic, where exact overlays appear only when real coordinates exist. The panel shows R1's marked value, its role derived from the verified topology ("current-limiting resistor for D1"), and its verified connections. From then on, every question carries R1 as context, so *"Why is this needed?"* just works.

## Debugging, one measurement at a time

Long lists of possible causes are not much use at the bench. CircuitSense computes an idealised DC operating point (a small modified-nodal-analysis solver, clearly labelled as a calculation rather than a simulation) and plans **the single most useful next check**. Take the LED sample. Ask *"The LED is dim. What should I measure next?"* and the agent asks you to measure VCC first, because a wrong supply invalidates every downstream reading. Reply *"I measured 4.2 V at VCC"* and the agent records the value. It compares it with the expected 5 V ± 5%, marks the deviation as a *confirmed* finding based on your measurement, and moves on: disconnect the load and measure the source's open-circuit voltage. Every step carries a safety note; resistance checks always start with "remove power", and circuits above 50 V trigger a hazardous-voltage warning.

## Testing a non-deterministic system deterministically

The repository ships three reference circuits (an LED indicator, a resistor divider and a non-inverting amplifier). Each has a hand-authored KiCad-format netlist that is validated against an independent adjacency table, plus known values and test questions with expected answers. There are 121 backend tests and 8 frontend tests. They cover upload validation, graph traversal, calculations, retrieval attribution, iteration limits, finding downgrades, and the Bedrock tool loop through fake boto3 clients, so the suite never needs live AWS access.

## Lessons learned

- **Put trust boundaries in the schema,** not just in the prompt.
- **Let Python do the maths** and let the model explain it.
- **Make the demo path share the real pipeline,** so the demo exercises the same tools, limits and guardrails as production.
- **Design for "I don't know".** Explicit uncertainty ("connectivity unavailable: upload a netlist") builds more trust than a fluent guess.

*Suggested screenshots (in `docs/screenshots/`): `01-welcome.png` (start screen), `03-focus.png` (Focus Component with verified connections), `02-feedback.png` (verified feedback path), `04-debug.png` (measurement-driven next step).*
