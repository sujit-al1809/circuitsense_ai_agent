import { useCallback, useEffect, useMemo, useState } from "react";
import { CircuitBoard, Info, MessageSquare, PanelLeft } from "lucide-react";
import { api, ApiError } from "./lib/api";
import type { ChatMessage, CircuitSession, Health, MeasurementCreate, SampleCircuitInfo } from "./lib/types";
import { Header } from "./components/Header";
import { AnalysisSummary, ComponentList, SampleList, UploadCard } from "./components/LeftPanel";
import { SchematicViewer } from "./components/SchematicViewer";
import { FocusPanel } from "./components/FocusPanel";
import { ChatPanel } from "./components/ChatPanel";
import { MeasurementForm } from "./components/MeasurementForm";
import { Welcome } from "./components/Welcome";
import { cx } from "./components/ui";

type Tab = "circuit" | "schematic" | "assistant";
const SESSION_KEY = "circuitsense.circuitId";
const DEFAULT_SUGGESTIONS = ["Explain this circuit.", "What should I measure next?", "Which connections are uncertain?"];

function errorMessage(e: unknown): string {
  return e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong.";
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [samples, setSamples] = useState<SampleCircuitInfo[]>([]);
  const [session, setSession] = useState<CircuitSession | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [sampleBusy, setSampleBusy] = useState<string | null>(null);
  const [chatBusy, setChatBusy] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("schematic");

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setHealthError(errorMessage(e)));
    api.samples().then(setSamples).catch(() => setSamples([]));
    let saved: string | null = null;
    try {
      saved = sessionStorage.getItem(SESSION_KEY);
    } catch {
      saved = null;
    }
    if (saved) {
      api.getCircuit(saved).then(setSession).catch(() => {
        try {
          sessionStorage.removeItem(SESSION_KEY);
        } catch {
          /* storage unavailable */
        }
      });
    }
  }, []);

  const adopt = useCallback((s: CircuitSession) => {
    setSession(s);
    setSelected(null);
    setChatError(null);
    setTab("schematic");
    try {
      sessionStorage.setItem(SESSION_KEY, s.analysis.circuit_id);
    } catch {
      /* storage unavailable */
    }
  }, []);

  const loadSample = useCallback(
    async (id: string): Promise<CircuitSession | null> => {
      setSampleBusy(id);
      setAnalyzeError(null);
      try {
        const s = await api.loadSample(id);
        adopt(s);
        return s;
      } catch (e) {
        setAnalyzeError(errorMessage(e));
        return null;
      } finally {
        setSampleBusy(null);
      }
    },
    [adopt],
  );

  const analyze = useCallback(
    async (file: File, netlist: File | null, page: number) => {
      setAnalyzing(true);
      setAnalyzeError(null);
      try {
        adopt(await api.analyze(file, netlist, page));
      } catch (e) {
        setAnalyzeError(errorMessage(e));
      } finally {
        setAnalyzing(false);
      }
    },
    [adopt],
  );

  const ask = useCallback(
    async (question: string, focusOverride?: string | null, target?: CircuitSession) => {
      const current = target ?? session;
      if (!current || chatBusy) return;
      const focus = focusOverride !== undefined ? focusOverride : selected;
      const userMsg: ChatMessage = {
        id: `local-${Date.now()}`,
        role: "user",
        content: question,
        created_at: new Date().toISOString(),
        focus_component: focus,
      };
      setSession((s) => (s ? { ...s, messages: [...s.messages, userMsg] } : s));
      setChatBusy(true);
      setChatError(null);
      setTab("assistant");
      try {
        let r;
        try {
          r = await api.chat(current.analysis.circuit_id, question, focus);
        } catch (e) {
          // Serverless hosts may route to an instance that never saw this in-memory session.
          // Bundled samples are deterministic, so reload the sample and retry once.
          const sampleId = current.analysis.sample_id;
          if (!(e instanceof ApiError && e.code === "circuit_not_found" && sampleId)) throw e;
          const fresh = await api.loadSample(sampleId);
          setSession((s) => (s ? { ...fresh, messages: s.messages, measurements: [] } : fresh));
          try {
            sessionStorage.setItem(SESSION_KEY, fresh.analysis.circuit_id);
          } catch {
            /* storage unavailable */
          }
          r = await api.chat(fresh.analysis.circuit_id, question, focus);
        }
        const reply: ChatMessage = { id: r.message_id, role: "assistant", content: r.answer, created_at: r.created_at, focus_component: focus, response: r };
        setSession((s) =>
          s
            ? {
                ...s,
                messages: [...s.messages, reply],
                measurements: [...s.measurements, ...r.measurements_recorded.filter((m) => !s.measurements.some((x) => x.id === m.id))],
              }
            : s,
        );
      } catch (e) {
        setChatError(errorMessage(e));
      } finally {
        setChatBusy(false);
      }
    },
    [session, chatBusy, selected],
  );

  const onExample = useCallback(
    async (question: string, sample: string) => {
      const s = await loadSample(sample);
      if (s) await ask(question, null, s);
    },
    [loadSample, ask],
  );

  const recordMeasurement = useCallback(
    async (m: MeasurementCreate) => {
      if (!session) return;
      const saved = await api.addMeasurement(session.analysis.circuit_id, m);
      setSession((s) => (s ? { ...s, measurements: [...s.measurements, saved] } : s));
    },
    [session],
  );

  const reset = () => {
    setSession(null);
    setSelected(null);
    try {
      sessionStorage.removeItem(SESSION_KEY);
    } catch {
      /* storage unavailable */
    }
  };

  const analysis = session?.analysis ?? null;
  const suggestions = useMemo(() => {
    if (!analysis) return [];
    return samples.find((s) => s.id === analysis.sample_id)?.example_questions ?? DEFAULT_SUGGESTIONS;
  }, [analysis, samples]);

  const tabs: { id: Tab; label: string; icon: typeof PanelLeft }[] = [
    { id: "circuit", label: "Circuit", icon: PanelLeft },
    { id: "schematic", label: "Schematic", icon: CircuitBoard },
    { id: "assistant", label: "Assistant", icon: MessageSquare },
  ];

  return (
    <div className="flex h-full flex-col">
      <Header health={health} healthError={healthError} onReset={reset} hasSession={!!session} />

      {analysis && analysis.notices.length > 0 && (
        <div className="flex items-start gap-2 border-b border-line bg-warn/5 px-4 py-1.5 text-[11px] text-ink-2" role="note">
          <Info className="mt-0.5 size-3.5 shrink-0 text-warn" aria-hidden />
          <span className="min-w-0">{analysis.notices.join(" ")}</span>
        </div>
      )}

      <nav className="flex border-b border-line bg-surface-1 lg:hidden" aria-label="Workspace sections">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            aria-current={tab === id ? "page" : undefined}
            className={cx("flex flex-1 items-center justify-center gap-1.5 py-2.5 text-xs font-medium", tab === id ? "border-b-2 border-accent text-accent" : "text-ink-3")}
          >
            <Icon className="size-3.5" /> {label}
          </button>
        ))}
      </nav>

      <main className="grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[300px_minmax(0,1fr)_400px] xl:grid-cols-[320px_minmax(0,1fr)_450px]">
        <aside className={cx("scroll-thin min-h-0 flex-col gap-3 overflow-y-auto lg:flex [&>*]:shrink-0", tab === "circuit" ? "flex" : "hidden")} aria-label="Circuit">
          <UploadCard busy={analyzing} error={analyzeError} onAnalyze={analyze} />
          {analysis ? (
            <>
              <AnalysisSummary analysis={analysis} />
              <ComponentList analysis={analysis} selected={selected} onSelect={setSelected} />
            </>
          ) : null}
          <SampleList samples={samples} activeId={analysis?.sample_id} busyId={sampleBusy} onLoad={loadSample} />
        </aside>

        <section className={cx("min-h-0 flex-col gap-3 lg:flex", tab === "schematic" ? "flex" : "hidden")} aria-label="Schematic">
          {analysis ? (
            <>
              <div className="min-h-[320px] flex-1">
                <SchematicViewer analysis={analysis} selected={selected} onSelect={setSelected} />
              </div>
              <FocusPanel analysis={analysis} selected={selected} busy={chatBusy} onAsk={(q, f) => ask(q, f)} onClear={() => setSelected(null)} />
            </>
          ) : (
            <Welcome samples={samples} busyId={sampleBusy} onLoadSample={loadSample} onExample={onExample} />
          )}
        </section>

        <aside className={cx("min-h-0 lg:flex lg:flex-col", tab === "assistant" ? "flex flex-col" : "hidden")} aria-label="Assistant">
          <ChatPanel
            messages={session?.messages ?? []}
            suggestions={suggestions}
            focus={selected}
            busy={chatBusy}
            error={chatError}
            disabled={!session}
            onAsk={(q) => ask(q)}
            onClearFocus={() => setSelected(null)}
            footer={
              analysis ? (
                <MeasurementForm key={analysis.circuit_id} analysis={analysis} measurements={session?.measurements ?? []} defaultTarget={selected} onRecord={recordMeasurement} />
              ) : null
            }
          />
        </aside>
      </main>
    </div>
  );
}
