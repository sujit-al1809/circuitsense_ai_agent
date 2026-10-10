import { Activity, CircuitBoard, RotateCcw } from "lucide-react";
import type { Health } from "../lib/types";
import { Badge, Spinner } from "./ui";

export function Header({ health, healthError, onReset, hasSession }: { health: Health | null; healthError: string | null; onReset: () => void; hasSession: boolean }) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-line bg-surface-1/80 px-4 backdrop-blur">
      <div className="flex items-center gap-2.5">
        <span className="grid size-8 place-items-center rounded-lg border border-accent/40 bg-accent-soft/50">
          <CircuitBoard className="size-4 text-accent" aria-hidden />
        </span>
        <div className="leading-tight">
          <p className="text-sm font-semibold tracking-tight text-ink-1">CircuitSense</p>
          <p className="hidden text-[11px] text-ink-3 sm:block">Evidence-based circuit debugging agent</p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        {healthError ? (
          <Badge tone="bad" title={healthError}>API offline</Badge>
        ) : !health ? (
          <Spinner className="size-4 text-ink-3" />
        ) : (
          <>
            <Badge
              tone={health.provider.mode === "demo" ? "warn" : health.provider.configured ? "accent" : "bad"}
              title={[health.provider.label, ...health.provider.issues, ...health.provider.notes].join("\n")}
            >
              <Activity className="size-3" />
              {health.provider.mode === "demo" ? "Demo mode" : health.provider.configured ? "Amazon Bedrock" : "Bedrock misconfigured"}
            </Badge>
            <span className="hidden md:inline-flex">
              <Badge title={health.simulation_available ? "ngspice available" : "ngspice not installed on the server"}>
                ngspice {health.simulation_available ? "on" : "off"}
              </Badge>
            </span>
          </>
        )}
        {hasSession && (
          <button type="button" className="btn-ghost px-2.5 py-1.5 text-xs" onClick={onReset}>
            <RotateCcw className="size-3.5" /> New
          </button>
        )}
      </div>
    </header>
  );
}
