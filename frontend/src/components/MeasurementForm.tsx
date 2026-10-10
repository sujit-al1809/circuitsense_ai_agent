import { useState, type FormEvent } from "react";
import { ChevronDown, ChevronRight, Ruler } from "lucide-react";
import type { CircuitAnalysis, Measurement, MeasurementCreate, Quantity } from "../lib/types";
import { Spinner } from "./ui";

const UNITS: Record<Quantity, string[]> = {
  voltage: ["V", "mV"],
  current: ["mA", "A", "uA"],
  resistance: ["Ω", "kΩ", "MΩ"],
  frequency: ["Hz", "kHz", "MHz"],
};

export function MeasurementForm({
  analysis,
  measurements,
  defaultTarget,
  onRecord,
}: {
  analysis: CircuitAnalysis;
  measurements: Measurement[];
  defaultTarget: string | null;
  onRecord: (m: MeasurementCreate) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [quantity, setQuantity] = useState<Quantity>("voltage");
  const [unit, setUnit] = useState("V");
  const [value, setValue] = useState("");
  const [target, setTarget] = useState(defaultTarget ?? "");
  const [reference, setReference] = useState("GND");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const targets = [...analysis.nets.map((n) => n.name), ...analysis.components.map((c) => c.ref)];
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const num = Number(value);
    if (!target.trim() || value.trim() === "" || !Number.isFinite(num)) {
      setError("Enter a numeric value and where you measured it.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onRecord({
        quantity,
        value: num,
        unit,
        target: target.trim(),
        reference: quantity === "voltage" && reference.trim() ? reference.trim() : null,
        power_state: quantity === "resistance" ? "unpowered" : "powered",
      });
      setValue("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border-t border-line px-3 py-2">
      <button type="button" className="flex w-full items-center gap-1.5 text-xs font-medium text-ink-2 hover:text-ink-1" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        <Ruler className="size-3.5 text-accent" /> Record a measurement
        {measurements.length > 0 && <span className="ml-auto text-[11px] text-ink-3">{measurements.length} recorded</span>}
      </button>
      {open && (
        <form onSubmit={submit} className="mt-2 space-y-2" aria-label="Record a measurement">
          <div className="grid grid-cols-[1fr_1fr_auto] gap-2">
            <label className="text-[11px] text-ink-3">
              Quantity
              <select
                className="input mt-0.5 py-1.5"
                value={quantity}
                onChange={(e) => {
                  const q = e.target.value as Quantity;
                  setQuantity(q);
                  setUnit(UNITS[q][0]);
                }}
              >
                <option value="voltage">Voltage</option>
                <option value="current">Current</option>
                <option value="resistance">Resistance (power off)</option>
                <option value="frequency">Frequency</option>
              </select>
            </label>
            <label className="text-[11px] text-ink-3">
              Value
              <input className="input mt-0.5 py-1.5" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} placeholder="4.2" />
            </label>
            <label className="text-[11px] text-ink-3">
              Unit
              <select className="input mt-0.5 py-1.5" value={unit} onChange={(e) => setUnit(e.target.value)}>
                {UNITS[quantity].map((u) => (
                  <option key={u}>{u}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-[11px] text-ink-3">
              Where (net, part or pin)
              <input className="input mt-0.5 py-1.5" list="measure-targets" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="VOUT, R1, U1.6" />
              <datalist id="measure-targets">
                {targets.map((t) => (
                  <option key={t} value={t} />
                ))}
              </datalist>
            </label>
            {quantity === "voltage" && (
              <label className="text-[11px] text-ink-3">
                Relative to
                <input className="input mt-0.5 py-1.5" value={reference} onChange={(e) => setReference(e.target.value)} />
              </label>
            )}
          </div>
          {error && <p role="alert" className="text-[11px] text-bad">{error}</p>}
          <div className="flex items-center justify-between gap-2">
            <p className="text-[10px] text-ink-3">Stored with a timestamp. CircuitSense never invents readings.</p>
            <button type="submit" className="btn-ghost px-3 py-1.5 text-xs" disabled={saving}>
              {saving && <Spinner className="size-3.5" />} Save
            </button>
          </div>
          {measurements.length > 0 && (
            <ul className="scroll-thin max-h-24 space-y-0.5 overflow-y-auto text-[11px] text-ink-2">
              {[...measurements].reverse().map((m) => (
                <li key={m.id} className="flex justify-between gap-2 font-mono">
                  <span>
                    {m.target}
                    {m.reference ? ` vs ${m.reference}` : ""}
                  </span>
                  <span>
                    {m.value} {m.unit}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </form>
      )}
    </div>
  );
}
