import { useEffect, useState, type FormEvent } from "react";
import { Crosshair, Send, ShieldAlert, X } from "lucide-react";
import { api } from "../lib/api";
import type { CircuitAnalysis, TraceResult, TracedNet } from "../lib/types";
import { Badge, ComponentIcon, ProvenanceBadge, Spinner, cx, prettyType } from "./ui";

export function focusQuestions(type: string, ref: string): string[] {
  const base = [`Why is ${ref} needed?`, `What should I measure to check ${ref}?`];
  if (type === "led") return [...base, "Calculate current through the LED."];
  if (type === "opamp") return [...base, "Trace the feedback path."];
  return [...base, `Show the datasheet limits for ${ref}.`];
}

function NetList({ nets, inferred }: { nets: TracedNet[]; inferred?: boolean }) {
  return (
    <ul className="space-y-1.5">
      {nets.map((n) => (
        <li key={`${n.net}-${n.via_pin ?? ""}`} className={cx("rounded-md border px-2.5 py-1.5", inferred ? "border-dashed border-inferred/40" : "border-line bg-surface-0/40")}>
          <div className="flex items-center gap-2 text-xs">
            {n.via_pin && (
              <span className="font-mono text-ink-3">
                pin {n.via_pin}
                {n.via_pin_name ? ` (${n.via_pin_name})` : ""}
              </span>
            )}
            <span className="font-mono font-semibold text-accent">{n.net}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {n.connections.length === 0 && <span className="text-[11px] text-ink-3">no other connections</span>}
            {n.connections.map((c) => (
              <span key={`${c.ref}.${c.pin}`} className="rounded border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-ink-2">
                {c.ref}.{c.pin}
                {c.pin_name ? <span className="text-ink-3"> {c.pin_name}</span> : null}
                {c.value ? <span className="text-ink-3"> · {c.value}</span> : null}
              </span>
            ))}
          </div>
        </li>
      ))}
    </ul>
  );
}

export function FocusPanel({
  analysis,
  selected,
  busy,
  onAsk,
  onClear,
}: {
  analysis: CircuitAnalysis;
  selected: string | null;
  busy: boolean;
  onAsk: (question: string, focus: string) => void;
  onClear: () => void;
}) {
  const [trace, setTrace] = useState<TraceResult | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [question, setQuestion] = useState("");
  const comp = analysis.components.find((c) => c.ref === selected) ?? null;

  useEffect(() => {
    setTrace(null);
    setTraceError(null);
    setQuestion("");
    if (!selected) return;
    let cancelled = false;
    setLoading(true);
    api
      .trace(analysis.circuit_id, selected)
      .then((t) => !cancelled && setTrace(t))
      .catch((e: Error) => !cancelled && setTraceError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [analysis.circuit_id, selected]);

  if (!comp) {
    return (
      <section aria-label="Focus component" className="panel flex items-center gap-3 p-3 text-xs text-ink-3">
        <Crosshair className="size-4 shrink-0 text-ink-3" aria-hidden />
        <span>
          <span className="font-medium text-ink-2">Focus Component.</span> Select a part in the list
          {analysis.components.some((c) => c.bbox) ? " or on the schematic" : ""} to see its verified connections and ask about it directly.
        </span>
      </section>
    );
  }

  const role = analysis.component_roles?.[comp.ref];
  const issues = analysis.issues.filter((i) => i.related_components.includes(comp.ref));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const q = question.trim();
    if (!q) return;
    onAsk(q, comp.ref);
    setQuestion("");
  };

  return (
    <section aria-label={`Focus component ${comp.ref}`} className="panel p-3" data-testid="focus-panel">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-lg border border-accent/50 bg-accent-soft/40 text-accent">
            <ComponentIcon type={comp.type} className="size-5" />
          </span>
          <div>
            <p className="flex items-baseline gap-2">
              <span className="font-mono text-lg font-semibold text-ink-1">{comp.ref}</span>
              <span className="text-sm text-ink-2">{comp.value ?? "value not marked"}</span>
            </p>
            <p className="text-xs text-ink-3">
              {prettyType(comp.type)}
              {comp.description ? ` · ${comp.description}` : ""}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <ProvenanceBadge kind={comp.provenance} />
          {comp.provenance === "image_inference" && <Badge tone="inferred">{Math.round(comp.confidence * 100)}% conf.</Badge>}
          <button type="button" className="btn-ghost px-1.5 py-1" aria-label="Clear focus" onClick={onClear}>
            <X className="size-3.5" />
          </button>
        </div>
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div className="space-y-2">
          <p className="panel-title">Role</p>
          {role ? (
            <p className="text-sm leading-snug text-ink-1">
              {role}
              <span className="mt-1 block text-[11px] text-ink-3">Derived by rule from the verified netlist topology.</span>
            </p>
          ) : (
            <p className="text-xs text-ink-3">Role unavailable – it can only be derived from verified connectivity.</p>
          )}
          {issues.map((i) => (
            <p key={i.id} className="flex items-start gap-1.5 rounded-md border border-warn/30 bg-warn/5 px-2 py-1.5 text-xs text-ink-2">
              <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-warn" aria-hidden />
              {i.title}
            </p>
          ))}
        </div>
        <div className="space-y-2">
          <p className="panel-title flex items-center gap-2">
            Connections
            {trace && (trace.verified ? <Badge tone="ok">verified</Badge> : <Badge tone="warn">not verified</Badge>)}
          </p>
          {loading && (
            <p className="flex items-center gap-2 text-xs text-ink-3">
              <Spinner className="size-3.5" /> Tracing…
            </p>
          )}
          {traceError && <p role="alert" className="text-xs text-bad">{traceError}</p>}
          {trace && trace.verified && trace.nets.length > 0 && <NetList nets={trace.nets} />}
          {trace && !trace.verified && (
            <div className="space-y-2">
              <p className="text-xs leading-snug text-ink-3">{trace.uncertainty}</p>
              {trace.inferred_hint.length > 0 && (
                <>
                  <p className="text-[11px] font-medium text-inferred">Inferred from image (unverified):</p>
                  <NetList nets={trace.inferred_hint} inferred />
                </>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="mt-3 border-t border-line pt-3">
        <div className="flex flex-wrap gap-1.5">
          {focusQuestions(comp.type, comp.ref).map((q) => (
            <button key={q} type="button" className="chip" disabled={busy} onClick={() => onAsk(q, comp.ref)}>
              {q}
            </button>
          ))}
        </div>
        <form onSubmit={submit} className="mt-2 flex gap-2">
          <label htmlFor="focus-question" className="sr-only">
            Ask about {comp.ref}
          </label>
          <input
            id="focus-question"
            className="input"
            placeholder={`Ask about ${comp.ref}… e.g. “Why is this needed?”`}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={4000}
          />
          <button type="submit" className="btn-primary px-3" disabled={busy || !question.trim()} aria-label={`Ask about ${comp.ref}`}>
            <Send className="size-4" />
          </button>
        </form>
      </div>
    </section>
  );
}
