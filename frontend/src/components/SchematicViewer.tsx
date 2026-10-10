import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Eye, EyeOff, FileText, Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import { api } from "../lib/api";
import type { CircuitAnalysis } from "../lib/types";
import { Badge, cx } from "./ui";

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 5;

export function SchematicViewer({
  analysis,
  selected,
  onSelect,
}: {
  analysis: CircuitAnalysis;
  selected: string | null;
  onSelect: (ref: string | null) => void;
}) {
  const preview = analysis.preview;
  const viewport = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [showOverlays, setShowOverlays] = useState(true);
  const [imgError, setImgError] = useState(false);
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null);

  const boxed = analysis.components.filter((c) => c.bbox && c.bbox.page === (preview?.page ?? 1));
  const hasBoxes = boxed.length > 0;
  const boxSource = boxed[0]?.bbox?.source;

  useLayoutEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const update = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [preview?.url]);

  useEffect(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setImgError(false);
  }, [analysis.circuit_id]);

  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      setZoom((z) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z * (e.deltaY < 0 ? 1.12 : 1 / 1.12))));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [preview?.url]);

  if (!preview) {
    return (
      <div className="panel schematic-grid flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
        <FileText className="size-7 text-ink-3" aria-hidden />
        <p className="text-sm text-ink-2">No schematic image for this session.</p>
        <p className="max-w-sm text-xs text-ink-3">
          {analysis.source_kind === "netlist"
            ? "This analysis comes from a netlist only. Use the component list to focus on a part – its verified connections appear below."
            : "No preview is available."}
        </p>
      </div>
    );
  }

  const pad = 24;
  const aspect = preview.width / preview.height;
  // Fall back to a nominal viewport until the first measurement arrives.
  const availW = Math.max((size.w || 800) - pad * 2, 50);
  const availH = Math.max((size.h || 600) - pad * 2, 50);
  const fitW = Math.min(availW, availH * aspect);
  const fitH = fitW / aspect;

  const onPointerDown = (e: ReactPointerEvent) => {
    if ((e.target as HTMLElement).closest("button")) return;
    drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: ReactPointerEvent) => {
    if (!drag.current) return;
    setPan({ x: drag.current.px + e.clientX - drag.current.x, y: drag.current.py + e.clientY - drag.current.y });
  };
  const endDrag = () => {
    drag.current = null;
  };
  const zoomBy = (f: number) => setZoom((z) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z * f)));

  return (
    <div className="panel flex h-full min-h-0 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium text-ink-1">{analysis.source_filename ?? analysis.title}</span>
          {preview.page_count > 1 && <Badge>page {preview.page} / {preview.page_count}</Badge>}
        </div>
        <div className="flex items-center gap-1" role="toolbar" aria-label="Schematic view controls">
          <button type="button" className="btn-ghost px-2 py-1" aria-label="Zoom out" onClick={() => zoomBy(1 / 1.25)}>
            <ZoomOut className="size-4" />
          </button>
          <span className="w-12 text-center font-mono text-xs text-ink-2" aria-live="polite">{Math.round(zoom * 100)}%</span>
          <button type="button" className="btn-ghost px-2 py-1" aria-label="Zoom in" onClick={() => zoomBy(1.25)}>
            <ZoomIn className="size-4" />
          </button>
          <button type="button" className="btn-ghost px-2 py-1" aria-label="Fit to view" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>
            <Maximize2 className="size-4" />
          </button>
          <button
            type="button"
            className="btn-ghost px-2 py-1 text-xs"
            disabled={!hasBoxes}
            aria-pressed={hasBoxes && showOverlays}
            title={hasBoxes ? "Toggle component overlays" : "Unavailable: no component coordinates for this image"}
            onClick={() => setShowOverlays((v) => !v)}
          >
            {hasBoxes && showOverlays ? <Eye className="size-4" /> : <EyeOff className="size-4" />}
            Overlays
          </button>
        </div>
      </div>

      <div
        ref={viewport}
        className="schematic-grid relative min-h-0 flex-1 cursor-grab touch-none overflow-hidden active:cursor-grabbing"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        data-testid="schematic-viewport"
      >
        {(
          <div
            className="absolute left-1/2 top-1/2 origin-center"
            style={{
              width: fitW,
              height: fitH,
              transform: `translate(calc(-50% + ${pan.x}px), calc(-50% + ${pan.y}px)) scale(${zoom})`,
            }}
          >
            {imgError ? (
              <div className="grid h-full w-full place-items-center rounded-md border border-bad/40 bg-surface-2 text-sm text-bad">
                The schematic preview could not be loaded.
              </div>
            ) : (
              <img
                src={api.previewUrl(preview.url)}
                alt={`Schematic: ${analysis.title}`}
                draggable={false}
                onError={() => setImgError(true)}
                className="h-full w-full select-none rounded-md shadow-[0_0_0_1px_var(--color-line-strong),0_20px_60px_-20px_rgba(0,0,0,0.8)]"
              />
            )}
            {hasBoxes && showOverlays &&
              boxed.map((c) => {
                const b = c.bbox!;
                const active = c.ref === selected;
                const inferred = b.source === "image_inference";
                return (
                  <button
                    key={c.ref}
                    type="button"
                    aria-label={`Select ${c.ref}${c.value ? ` (${c.value})` : ""}`}
                    aria-pressed={active}
                    onClick={() => onSelect(active ? null : c.ref)}
                    className={cx(
                      "group absolute rounded-[3px] border-2 transition-colors",
                      active
                        ? "border-accent bg-accent/15 shadow-[0_0_0_4px_rgba(34,211,238,0.18)]"
                        : inferred
                          ? "border-dashed border-inferred/60 hover:border-inferred hover:bg-inferred/10"
                          : "border-accent/0 hover:border-accent/70 hover:bg-accent/10",
                    )}
                    style={{ left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.width * 100}%`, height: `${b.height * 100}%` }}
                  >
                    <span
                      className={cx(
                        "absolute -top-2.5 left-1 -translate-y-full rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold leading-none",
                        active ? "bg-accent text-surface-0" : "bg-surface-0/85 text-ink-1 opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100",
                      )}
                    >
                      {c.ref}
                    </span>
                  </button>
                );
              })}
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-3 py-1.5 text-[11px] text-ink-3">
        <span>Drag to pan · scroll to zoom</span>
        {hasBoxes ? (
          <span>
            Boxes: {boxSource === "sample_annotation" ? "exact sample annotations" : "approximate (vision model, unverified)"}
          </span>
        ) : (
          <span>No component coordinates – select parts from the list</span>
        )}
      </div>
    </div>
  );
}
