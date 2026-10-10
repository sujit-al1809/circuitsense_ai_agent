import { BookOpen, Calculator, CircuitBoard, Route, Sparkles, Target, Waypoints } from "lucide-react";
import type { SampleCircuitInfo } from "../lib/types";
import { Spinner } from "./ui";

export const EXAMPLE_QUESTIONS: { question: string; sample: string }[] = [
  { question: "Explain this circuit.", sample: "led_basic" },
  { question: "Find the role of R3.", sample: "noninverting_amp" },
  { question: "Investigate low output voltage.", sample: "resistor_divider" },
  { question: "Trace the feedback path.", sample: "noninverting_amp" },
  { question: "Calculate current through the LED.", sample: "led_basic" },
];

const PILLARS = [
  { icon: Waypoints, title: "Verified connectivity", text: "Netlists become a graph; traces never come from guessing at pixels." },
  { icon: Calculator, title: "Deterministic maths", text: "Ohm's law, dividers and gains are computed in Python, with assumptions shown." },
  { icon: BookOpen, title: "Cited datasheets", text: "Answers quote the passage, file, section and page they rely on." },
  { icon: Target, title: "One next check", text: "Debugging focuses on the single most useful measurement, and why." },
];

export function Welcome({
  samples,
  busyId,
  onLoadSample,
  onExample,
}: {
  samples: SampleCircuitInfo[];
  busyId: string | null;
  onLoadSample: (id: string) => void;
  onExample: (question: string, sample: string) => void;
}) {
  return (
    <div className="panel schematic-grid relative flex h-full min-h-0 flex-col overflow-y-auto scroll-thin">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,rgba(34,211,238,0.12),transparent_60%)]" />
      <div className="relative mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center gap-7 px-6 py-10">
        <div>
          <p className="mb-3 inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent-soft/40 px-3 py-1 text-[11px] font-medium text-accent">
            <Sparkles className="size-3" /> Agentic circuit analysis
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-ink-1 md:text-4xl">
            Understand, explain and debug circuits <span className="text-accent">with evidence.</span>
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-2">
            Upload a schematic, PDF or KiCad netlist. CircuitSense identifies components, traces verified connections, retrieves datasheet facts and
            plans your next measurement – and tells you exactly what is confirmed and what is still a hypothesis.
          </p>
        </div>

        <div>
          <p className="panel-title mb-2">Try an example question</p>
          <div className="flex flex-wrap gap-2">
            {EXAMPLE_QUESTIONS.map((e) => (
              <button key={e.question} type="button" className="chip text-sm" disabled={busyId !== null} onClick={() => onExample(e.question, e.sample)}>
                <Route className="size-3.5 text-accent" /> {e.question}
              </button>
            ))}
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          {samples.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => onLoadSample(s.id)}
              disabled={busyId !== null}
              className="card group flex flex-col gap-1.5 p-3 text-left transition-colors hover:border-accent/60 hover:bg-surface-3 disabled:opacity-60"
            >
              <span className="flex items-center justify-between">
                <CircuitBoard className="size-4 text-accent" aria-hidden />
                {busyId === s.id && <Spinner className="size-3.5 text-accent" />}
              </span>
              <span className="text-sm font-medium text-ink-1">{s.name}</span>
              <span className="text-[11px] leading-snug text-ink-3">{s.description}</span>
              <span className="mt-auto pt-1 text-[11px] font-medium text-accent opacity-80 group-hover:opacity-100">Open reference circuit →</span>
            </button>
          ))}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {PILLARS.map(({ icon: Icon, title, text }) => (
            <div key={title} className="flex gap-3 rounded-lg border border-line bg-surface-1/70 p-3">
              <Icon className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
              <div>
                <p className="text-sm font-medium text-ink-1">{title}</p>
                <p className="text-xs leading-snug text-ink-3">{text}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
