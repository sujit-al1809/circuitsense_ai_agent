import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import {
  BookOpen,
  Calculator,
  ChevronDown,
  ChevronRight,
  CircleCheck,
  CircleHelp,
  Crosshair,
  FlaskConical,
  Lightbulb,
  MessageSquare,

  Send,
  ShieldAlert,
  Sparkles,
  Target,
  TriangleAlert,
  Wrench,
  X,
} from "lucide-react";
import type { ChatMessage, ChatResponse, Citation } from "../lib/types";
import { Markdown } from "./Markdown";
import { Badge, PROVENANCE_LABEL, Spinner } from "./ui";

function Collapsible({ icon, title, count, children, defaultOpen = false }: { icon: ReactNode; title: string; count?: number; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-md border border-line bg-surface-0/40">
      <button type="button" className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-xs text-ink-2 hover:text-ink-1" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        {icon}
        <span className="font-medium">{title}</span>
        {count !== undefined && <span className="text-ink-3">({count})</span>}
      </button>
      {open && <div className="border-t border-line px-2.5 py-2">{children}</div>}
    </div>
  );
}

function CitationItem({ c }: { c: Citation }) {
  return (
    <li className="space-y-1">
      <p className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="font-medium text-ink-1">{c.title}</span>
        {c.illustrative ? <Badge tone="warn" title="Teaching data, not a manufacturer datasheet">illustrative</Badge> : <Badge tone="accent">reference</Badge>}
      </p>
      <p className="font-mono text-[11px] text-ink-3">
        {c.source}
        {c.page ? ` · p.${c.page}` : ""}
        {c.section ? ` · ${c.section}` : ""}
      </p>
      <blockquote className="border-l-2 border-accent/40 pl-2 text-[11px] leading-relaxed text-ink-2">{c.excerpt}</blockquote>
    </li>
  );
}

function AssistantMessage({ r, onAsk, busy }: { r: ChatResponse; onAsk: (q: string) => void; busy: boolean }) {
  return (
    <div className="space-y-2.5" data-testid="assistant-message">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={r.mode === "demo" ? "warn" : "accent"}>{r.mode === "demo" ? "Demo mode · rule-based" : "Amazon Bedrock"}</Badge>
        {r.focus_component && <Badge tone="accent"><Crosshair className="size-3" /> {r.focus_component}</Badge>}
        {r.stopped_reason !== "final" && <Badge tone="bad">stopped: {r.stopped_reason.replace(/_/g, " ")}</Badge>}
      </div>
      <Markdown text={r.answer} />

      {(r.confirmed.length > 0 || r.hypotheses.length > 0) && (
        <div className="grid gap-2">
          {r.confirmed.length > 0 && (
            <div className="rounded-md border border-ok/30 bg-ok/5 px-2.5 py-2">
              <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-ok">
                <CircleCheck className="size-3.5" /> Confirmed
              </p>
              <ul className="space-y-1">
                {r.confirmed.map((f, i) => (
                  <li key={i} className="flex flex-wrap items-start justify-between gap-x-2 text-xs text-ink-1">
                    <span className="min-w-0 flex-1">{f.statement}</span>
                    <span className="text-[10px] text-ink-3">{PROVENANCE_LABEL[f.basis]?.label ?? f.basis}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {r.hypotheses.length > 0 && (
            <div className="rounded-md border border-warn/30 bg-warn/5 px-2.5 py-2">
              <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-warn">
                <CircleHelp className="size-3.5" /> Hypotheses – not yet confirmed
              </p>
              <ul className="space-y-1">
                {r.hypotheses.map((f, i) => (
                  <li key={i} className="text-xs text-ink-2">{f.statement}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {r.next_step && (
        <div className="rounded-lg border border-accent/40 bg-accent-soft/25 px-3 py-2.5" data-testid="next-step">
          <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-accent">
            <Target className="size-3.5" /> Next diagnostic step
          </p>
          <p className="mt-1 text-sm text-ink-1">{r.next_step.action}</p>
          {r.next_step.expected && <p className="mt-1 text-xs text-ink-2">Expected: <span className="font-mono text-ink-1">{r.next_step.expected}</span></p>}
          <p className="mt-1 text-xs text-ink-3">Why: {r.next_step.reason}</p>
          {r.next_step.safety_note && (
            <p className="mt-1.5 flex items-start gap-1.5 text-[11px] text-warn">
              <ShieldAlert className="mt-0.5 size-3 shrink-0" /> {r.next_step.safety_note}
            </p>
          )}
        </div>
      )}

      <div className="space-y-1.5">
        {r.citations.length > 0 && (
          <Collapsible icon={<BookOpen className="size-3.5" />} title="Datasheet sources" count={r.citations.length}>
            <ul className="space-y-3">{r.citations.map((c) => <CitationItem key={c.id} c={c} />)}</ul>
          </Collapsible>
        )}
        {r.calculations.length > 0 && (
          <Collapsible icon={<Calculator className="size-3.5" />} title="Calculations" count={r.calculations.length}>
            {r.calculations.map((c, i) => (
              <div key={i} className="space-y-1 text-[11px] text-ink-2">
                <p className="font-mono text-ink-1">{c.formula}</p>
                <ul className="font-mono">{c.steps.map((s, k) => <li key={k}>{s}</li>)}</ul>
                {c.assumptions.length > 0 && <p className="text-ink-3">Assumptions: {c.assumptions.join(" ")}</p>}
              </div>
            ))}
          </Collapsible>
        )}
        {r.tool_calls.length > 0 && (
          <Collapsible icon={<Wrench className="size-3.5" />} title={`Agent trace · ${r.iterations} step${r.iterations === 1 ? "" : "s"}`} count={r.tool_calls.length}>
            <ol className="space-y-1">
              {r.tool_calls.map((t, i) => (
                <li key={i} className="text-[11px]">
                  <span className="font-mono text-accent">{t.name}</span>{" "}
                  <Badge tone={t.status === "ok" ? "ok" : t.status === "unavailable" ? "warn" : "bad"}>{t.status}</Badge>
                  <span className="ml-1 text-ink-3">{t.duration_ms.toFixed(0)} ms</span>
                  <p className="mt-0.5 text-ink-2">{t.summary}</p>
                </li>
              ))}
            </ol>
          </Collapsible>
        )}
      </div>

      {r.warnings.length > 0 && (
        <ul className="space-y-1">
          {r.warnings.map((w, i) => (
            <li key={i} className="flex items-start gap-1.5 text-[11px] text-warn">
              <TriangleAlert className="mt-0.5 size-3 shrink-0" /> {w}
            </li>
          ))}
        </ul>
      )}

      {r.follow_up_questions.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {r.follow_up_questions.map((q) => (
            <button key={q} type="button" className="chip" disabled={busy} onClick={() => onAsk(q)}>
              <Sparkles className="size-3 text-accent" /> {q}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function ChatPanel({
  messages,
  suggestions,
  focus,
  busy,
  error,
  disabled,
  onAsk,
  onClearFocus,
  footer,
}: {
  messages: ChatMessage[];
  suggestions: string[];
  focus: string | null;
  busy: boolean;
  error: string | null;
  disabled: boolean;
  onAsk: (q: string) => void;
  onClearFocus: () => void;
  footer?: ReactNode;
}) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [messages.length, busy]);

  const send = (e?: FormEvent) => {
    e?.preventDefault();
    const q = draft.trim();
    if (!q || busy || disabled) return;
    onAsk(q);
    setDraft("");
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <section aria-label="Circuit assistant" className="panel flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2.5">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-ink-1">
          <MessageSquare className="size-4 text-accent" aria-hidden /> Circuit assistant
        </h2>
        <span className="text-[11px] text-ink-3">evidence-first answers</span>
      </div>

      <div className="scroll-thin min-h-0 flex-1 space-y-4 overflow-y-auto px-3 py-3" aria-live="polite">
        {messages.length === 0 && !busy && (
          <div className="space-y-3 py-2">
            <div className="flex items-start gap-2.5 rounded-lg border border-line bg-surface-2 p-3">
              <Lightbulb className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
              <p className="text-xs leading-relaxed text-ink-2">
                Ask how the circuit works, what a part does, or why something misbehaves. Answers separate <span className="text-ok">confirmed findings</span> from{" "}
                <span className="text-warn">hypotheses</span> and cite their evidence.
              </p>
            </div>
            {suggestions.length > 0 && (
              <div>
                <p className="panel-title mb-1.5">Suggested questions</p>
                <div className="flex flex-wrap gap-1.5">
                  {suggestions.map((q) => (
                    <button key={q} type="button" className="chip" disabled={disabled} onClick={() => onAsk(q)}>
                      <Sparkles className="size-3 text-accent" /> {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
        {messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="ml-8 rounded-lg rounded-tr-sm border border-line-strong bg-surface-3 px-3 py-2 text-sm text-ink-1">
              {m.focus_component && (
                <span className="mb-1 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-accent">
                  <Crosshair className="size-3" /> {m.focus_component}
                </span>
              )}
              {m.content}
            </div>
          ) : m.response ? (
            <AssistantMessage key={m.id} r={m.response} onAsk={onAsk} busy={busy || disabled} />
          ) : (
            <div key={m.id} className="text-sm text-ink-2">{m.content}</div>
          ),
        )}
        {busy && (
          <div className="flex items-center gap-2 text-xs text-ink-3" role="status">
            <Spinner className="size-4 text-accent" /> Agent is working – choosing tools and checking evidence…
          </div>
        )}
        {error && (
          <p role="alert" className="flex items-start gap-1.5 rounded-md border border-bad/40 bg-bad/10 px-2.5 py-2 text-xs text-bad">
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0" /> {error}
          </p>
        )}
        <div ref={endRef} />
      </div>

      {footer}

      <form onSubmit={send} className="border-t border-line p-3">
        {focus && (
          <div className="mb-2 flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full border border-accent/50 bg-accent-soft/40 px-2.5 py-1 text-xs text-accent">
              <Crosshair className="size-3" /> Focused on {focus}
              <button type="button" aria-label="Clear focused component" onClick={onClearFocus} className="text-accent/80 hover:text-ink-1">
                <X className="size-3" />
              </button>
            </span>
            <span className="text-[11px] text-ink-3">Questions include this component as context.</span>
          </div>
        )}
        <div className="flex items-end gap-2">
          <label htmlFor="chat-input" className="sr-only">Ask a question about the circuit</label>
          <textarea
            id="chat-input"
            rows={2}
            className="input scroll-thin resize-none"
            placeholder={disabled ? "Load or upload a circuit first" : focus ? `Ask about ${focus}…` : "Ask about the circuit… (Enter to send)"}
            value={draft}
            disabled={disabled}
            maxLength={4000}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKey}
          />
          <button type="submit" className="btn-primary h-10 px-3" disabled={busy || disabled || !draft.trim()} aria-label="Send question">
            {busy ? <Spinner /> : <Send className="size-4" />}
          </button>
        </div>
        <p className="mt-1.5 flex items-center gap-1 text-[10px] text-ink-3">
          <FlaskConical className="size-3" /> Advisory only. Remove power before resistance checks; treat mains and high-energy circuits with qualified care.
        </p>
      </form>
    </section>
  );
}
