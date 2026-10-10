import type { ReactNode } from "react";
import {
  Battery,
  Cable,
  CircleDot,
  Cpu,
  Lightbulb,
  LoaderCircle,
  Microchip,
  Plug,
  Spline,
  Waves,
  Zap,
} from "lucide-react";
import type { ConnectivityStatus, Provenance } from "../lib/types";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

type Tone = "neutral" | "accent" | "ok" | "warn" | "bad" | "inferred";

const TONES: Record<Tone, string> = {
  neutral: "border-line bg-surface-3/60 text-ink-2",
  accent: "border-accent/40 bg-accent-soft/60 text-accent",
  ok: "border-ok/40 bg-ok/10 text-ok",
  warn: "border-warn/40 bg-warn/10 text-warn",
  bad: "border-bad/40 bg-bad/10 text-bad",
  inferred: "border-inferred/40 bg-inferred/10 text-inferred",
};

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium", TONES[tone])}
    >
      {children}
    </span>
  );
}

export const PROVENANCE_LABEL: Record<Provenance, { label: string; tone: Tone; help: string }> = {
  parsed_netlist: { label: "Verified netlist", tone: "ok", help: "Parsed from a structured netlist file" },
  image_inference: { label: "Image inference", tone: "inferred", help: "Read from the image by a vision model – unverified" },
  sample_annotation: { label: "Sample annotation", tone: "neutral", help: "Exact coordinates from the sample drawing generator" },
  datasheet: { label: "Datasheet", tone: "accent", help: "Retrieved from the local datasheet collection" },
  simulation: { label: "Simulation", tone: "accent", help: "Result of an actual ngspice run" },
  calculation: { label: "Calculation", tone: "accent", help: "Deterministic Python calculation" },
  user_measurement: { label: "Your measurement", tone: "warn", help: "A value you reported" },
};

export function ProvenanceBadge({ kind }: { kind: Provenance }) {
  const p = PROVENANCE_LABEL[kind];
  return (
    <Badge tone={p.tone} title={p.help}>
      {p.label}
    </Badge>
  );
}

export function ConnectivityBadge({ status }: { status: ConnectivityStatus }) {
  if (status === "verified") return <Badge tone="ok" title="Connectivity parsed from a netlist">Connectivity verified</Badge>;
  if (status === "inferred") return <Badge tone="inferred" title="Read from the image; not verified">Connectivity inferred</Badge>;
  return <Badge tone="warn" title="No connectivity information">Connectivity unavailable</Badge>;
}

export function Spinner({ className }: { className?: string }) {
  return <LoaderCircle aria-hidden className={cx("animate-spin", className ?? "size-4")} />;
}

export function ComponentIcon({ type, className }: { type: string; className?: string }) {
  const cls = className ?? "size-4";
  switch (type) {
    case "resistor":
    case "potentiometer":
      return <Spline aria-hidden className={cls} />;
    case "capacitor":
    case "inductor":
      return <Waves aria-hidden className={cls} />;
    case "led":
      return <Lightbulb aria-hidden className={cls} />;
    case "diode":
    case "zener":
      return <Zap aria-hidden className={cls} />;
    case "voltage_source":
    case "battery":
    case "current_source":
      return <Battery aria-hidden className={cls} />;
    case "opamp":
      return <Cpu aria-hidden className={cls} />;
    case "ic":
    case "transistor":
    case "mosfet":
      return <Microchip aria-hidden className={cls} />;
    case "connector":
      return <Plug aria-hidden className={cls} />;
    case "switch":
    case "fuse":
      return <Cable aria-hidden className={cls} />;
    default:
      return <CircleDot aria-hidden className={cls} />;
  }
}

export function SectionTitle({ icon, children, right }: { icon?: ReactNode; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between gap-2">
      <h2 className="panel-title flex items-center gap-1.5">
        {icon}
        {children}
      </h2>
      {right}
    </div>
  );
}

export function prettyType(type: string) {
  return type.replace(/_/g, " ");
}
