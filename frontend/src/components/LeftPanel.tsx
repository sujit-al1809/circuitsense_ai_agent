import { useRef, useState, type DragEvent } from "react";
import {
  ChevronDown,
  ChevronRight,
  CircuitBoard,
  FileUp,
  Info,
  ListChecks,
  Network,
  Paperclip,
  ShieldAlert,
  TriangleAlert,
  Upload,
  X,
} from "lucide-react";
import type { CircuitAnalysis, SampleCircuitInfo } from "../lib/types";
import { Badge, ComponentIcon, ConnectivityBadge, ProvenanceBadge, SectionTitle, Spinner, cx, prettyType } from "./ui";

const ACCEPT_MAIN = ".png,.jpg,.jpeg,.webp,.pdf,.net,image/png,image/jpeg,image/webp,application/pdf";
const MAX_MB = 10;

export function UploadCard({
  busy,
  error,
  onAnalyze,
}: {
  busy: boolean;
  error: string | null;
  onAnalyze: (file: File, netlist: File | null, page: number) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [netlist, setNetlist] = useState<File | null>(null);
  const [page, setPage] = useState(1);
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const netInput = useRef<HTMLInputElement>(null);

  const pick = (f: File | undefined | null) => {
    if (!f) return;
    if (f.size > MAX_MB * 1024 * 1024) {
      setLocalError(`${f.name} is larger than ${MAX_MB} MB.`);
      return;
    }
    setLocalError(null);
    setFile(f);
    setPage(1);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    pick(e.dataTransfer.files?.[0]);
  };
  const isPdf = file?.name.toLowerCase().endsWith(".pdf");
  const isNet = file?.name.toLowerCase().endsWith(".net");

  return (
    <section aria-labelledby="upload-title" className="panel p-3">
      <SectionTitle icon={<Upload className="size-3.5" />}>
        <span id="upload-title">Upload schematic</span>
      </SectionTitle>
      <button
        type="button"
        onClick={() => fileInput.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cx(
          "flex w-full flex-col items-center gap-1.5 rounded-lg border border-dashed px-3 py-4 text-center transition-colors",
          dragging ? "border-accent bg-accent-soft/40" : "border-line-strong hover:border-accent/60 hover:bg-surface-2",
        )}
      >
        <FileUp className="size-5 text-accent" aria-hidden />
        <span className="text-sm text-ink-1">{file ? file.name : "Drop a file or browse"}</span>
        <span className="text-[11px] text-ink-3">PNG · JPEG · WebP · PDF · KiCad netlist (.net) · max {MAX_MB} MB</span>
      </button>
      <input
        ref={fileInput}
        type="file"
        accept={ACCEPT_MAIN}
        className="sr-only"
        aria-label="Schematic file"
        data-testid="file-input"
        onChange={(e) => pick(e.target.files?.[0])}
      />
      {file && !isNet && (
        <div className="mt-2 space-y-2">
          <div className="flex items-center gap-2">
            <button type="button" className="btn-ghost flex-1 px-2 py-1.5 text-xs" onClick={() => netInput.current?.click()}>
              <Paperclip className="size-3.5" aria-hidden />
              {netlist ? netlist.name : "Attach KiCad netlist (optional)"}
            </button>
            {netlist && (
              <button type="button" aria-label="Remove netlist" className="btn-ghost px-2 py-1.5" onClick={() => setNetlist(null)}>
                <X className="size-3.5" />
              </button>
            )}
          </div>
          <input ref={netInput} type="file" accept=".net" className="sr-only" aria-label="KiCad netlist" onChange={(e) => setNetlist(e.target.files?.[0] ?? null)} />
          {isPdf && (
            <label className="flex items-center gap-2 text-xs text-ink-2">
              PDF page
              <input type="number" min={1} max={500} value={page} onChange={(e) => setPage(Math.max(1, Number(e.target.value) || 1))} className="input w-20 py-1" />
            </label>
          )}
          <p className="text-[11px] leading-snug text-ink-3">A netlist gives verified connectivity; an image alone only gives inferred connections.</p>
        </div>
      )}
      {(localError || error) && (
        <p role="alert" className="mt-2 flex items-start gap-1.5 rounded-md border border-bad/40 bg-bad/10 px-2 py-1.5 text-xs text-bad">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          {localError || error}
        </p>
      )}
      <button type="button" className="btn-primary mt-3 w-full" disabled={!file || busy} onClick={() => file && onAnalyze(file, netlist, page)}>
        {busy ? <Spinner /> : <CircuitBoard className="size-4" aria-hidden />}
        {busy ? "Analyzing…" : "Analyze circuit"}
      </button>
    </section>
  );
}

export function SampleList({
  samples,
  activeId,
  busyId,
  onLoad,
}: {
  samples: SampleCircuitInfo[];
  activeId?: string | null;
  busyId: string | null;
  onLoad: (id: string) => void;
}) {
  if (samples.length === 0) return null;
  return (
    <section aria-labelledby="samples-title" className="panel p-3">
      <SectionTitle icon={<CircuitBoard className="size-3.5" />}>
        <span id="samples-title">Reference circuits</span>
      </SectionTitle>
      <ul className="space-y-1.5">
        {samples.map((s) => (
          <li key={s.id}>
            <button
              type="button"
              onClick={() => onLoad(s.id)}
              disabled={busyId !== null}
              className={cx(
                "w-full rounded-lg border px-3 py-2 text-left transition-colors",
                activeId === s.id ? "border-accent/60 bg-accent-soft/30" : "border-line bg-surface-2 hover:border-line-strong hover:bg-surface-3",
              )}
            >
              <span className="flex items-center justify-between gap-2 text-sm font-medium text-ink-1">
                {s.name}
                {busyId === s.id && <Spinner className="size-3.5 text-accent" />}
              </span>
              <span className="mt-0.5 block text-[11px] leading-snug text-ink-3">{s.description}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function AnalysisSummary({ analysis }: { analysis: CircuitAnalysis }) {
  const [showUnc, setShowUnc] = useState(false);
  return (
    <section aria-labelledby="analysis-title" className="panel p-3">
      <SectionTitle icon={<ListChecks className="size-3.5" />}>
        <span id="analysis-title">Analysis</span>
      </SectionTitle>
      <h3 className="text-sm font-semibold text-ink-1">{analysis.title}</h3>
      <div className="mt-1.5 flex flex-wrap gap-1">
        <ConnectivityBadge status={analysis.connectivity_status} />
        <Badge>{prettyType(analysis.circuit_type)}</Badge>
      </div>
      {analysis.summary && <p className="mt-2 text-xs leading-relaxed text-ink-2">{analysis.summary}</p>}
      <div className="mt-2 flex flex-wrap gap-1" aria-label="Evidence sources">
        {analysis.provenance.map((p) => (
          <ProvenanceBadge key={p} kind={p} />
        ))}
      </div>

      {analysis.issues.length > 0 && (
        <div className="mt-3 space-y-1.5">
          <p className="panel-title">Potential issues</p>
          {analysis.issues.map((i) => (
            <div key={i.id} className={cx("rounded-md border px-2 py-1.5 text-xs", i.severity === "critical" ? "border-bad/40 bg-bad/10" : "border-warn/30 bg-warn/5")}>
              <p className="flex items-center gap-1.5 font-medium text-ink-1">
                <ShieldAlert className={cx("size-3.5", i.severity === "critical" ? "text-bad" : "text-warn")} aria-hidden />
                {i.title}
                <Badge tone={i.status === "confirmed" ? "ok" : "warn"}>{i.status}</Badge>
              </p>
              <p className="mt-1 text-ink-2">{i.description}</p>
              <ul className="mt-1 space-y-0.5 text-[11px] text-ink-3">
                {i.evidence.map((e, k) => (
                  <li key={k}>
                    Evidence ({e.kind.replace(/_/g, " ")}): {e.detail}
                    {e.source ? ` — ${e.source}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {analysis.uncertainties.length > 0 && (
        <div className="mt-3">
          <button type="button" className="flex items-center gap-1 text-xs text-ink-2 hover:text-ink-1" aria-expanded={showUnc} onClick={() => setShowUnc((v) => !v)}>
            {showUnc ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
            Uncertainties &amp; missing information ({analysis.uncertainties.length})
          </button>
          {showUnc && (
            <ul className="mt-1.5 space-y-1 text-[11px] leading-snug text-ink-3">
              {analysis.uncertainties.map((u, k) => (
                <li key={k} className="flex gap-1.5">
                  <Info className="mt-0.5 size-3 shrink-0" aria-hidden />
                  <span>
                    <span className="text-ink-2">{u.topic}:</span> {u.detail}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

export function ComponentList({
  analysis,
  selected,
  onSelect,
}: {
  analysis: CircuitAnalysis;
  selected: string | null;
  onSelect: (ref: string | null) => void;
}) {
  const comps = analysis.components;
  return (
    <section aria-labelledby="components-title" className="panel flex min-h-0 flex-col p-3">
      <SectionTitle icon={<Network className="size-3.5" />} right={<span className="text-[11px] text-ink-3">{comps.length}</span>}>
        <span id="components-title">Components</span>
      </SectionTitle>
      {comps.length === 0 ? (
        <p className="rounded-md border border-dashed border-line px-3 py-4 text-center text-xs text-ink-3">No components detected.</p>
      ) : (
        <ul className="scroll-thin -mx-1 min-h-0 space-y-0.5 overflow-y-auto px-1" aria-label="Detected components">
          {comps.map((c) => {
            const active = c.ref === selected;
            return (
              <li key={c.ref}>
                <button
                  type="button"
                  data-testid={`component-${c.ref}`}
                  aria-pressed={active}
                  onClick={() => onSelect(active ? null : c.ref)}
                  className={cx(
                    "flex w-full items-center gap-2.5 rounded-md border px-2 py-1.5 text-left transition-colors",
                    active ? "border-accent/60 bg-accent-soft/40" : "border-transparent hover:bg-surface-2",
                  )}
                >
                  <span className={cx("grid size-7 shrink-0 place-items-center rounded-md border", active ? "border-accent/50 text-accent" : "border-line text-ink-2")}>
                    <ComponentIcon type={c.type} className="size-3.5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline gap-2">
                      <span className="font-mono text-sm font-semibold text-ink-1">{c.ref}</span>
                      <span className="truncate text-xs text-ink-2">{c.value ?? "—"}</span>
                    </span>
                    <span className="block truncate text-[11px] text-ink-3">{prettyType(c.type)}</span>
                  </span>
                  <span className="flex flex-col items-end gap-0.5">
                    <span
                      className={cx("h-1.5 w-1.5 rounded-full", c.provenance === "parsed_netlist" ? "bg-ok" : "bg-inferred")}
                      title={c.provenance === "parsed_netlist" ? "From verified netlist" : `Image inference, confidence ${Math.round(c.confidence * 100)}%`}
                    />
                    {c.provenance !== "parsed_netlist" && <span className="text-[10px] text-ink-3">{Math.round(c.confidence * 100)}%</span>}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
