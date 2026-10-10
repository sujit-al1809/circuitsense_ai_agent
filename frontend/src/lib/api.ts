import type {
  ChatResponse,
  CircuitSession,
  Health,
  Measurement,
  MeasurementCreate,
  SampleCircuitInfo,
  TraceResult,
} from "./types";

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new ApiError("Cannot reach the CircuitSense API. Is the backend running?", 0, "network_error");
  }
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!res.ok) {
    const err = (body as { error?: { message?: string; code?: string } } | null)?.error;
    throw new ApiError(err?.message ?? `Request failed (${res.status})`, res.status, err?.code ?? "http_error");
  }
  return body as T;
}

const json = (payload: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
});

export const api = {
  health: () => request<Health>("/health"),
  samples: () => request<SampleCircuitInfo[]>("/api/samples"),
  loadSample: (id: string) => request<CircuitSession>(`/api/samples/${encodeURIComponent(id)}/load`, { method: "POST" }),
  analyze: (file: File, netlist?: File | null, page = 1) => {
    const form = new FormData();
    form.append("file", file);
    if (netlist) form.append("netlist", netlist);
    form.append("page", String(page));
    return request<CircuitSession>("/api/circuits/analyze", { method: "POST", body: form });
  },
  getCircuit: (id: string) => request<CircuitSession>(`/api/circuits/${encodeURIComponent(id)}`),
  trace: (id: string, target: string, to?: string) =>
    request<TraceResult>(`/api/circuits/${encodeURIComponent(id)}/trace`, json({ target, to })),
  addMeasurement: (id: string, m: MeasurementCreate) =>
    request<Measurement>(`/api/circuits/${encodeURIComponent(id)}/measurements`, json(m)),
  chat: (circuitId: string, message: string, focus?: string | null) =>
    request<ChatResponse>("/api/chat", json({ circuit_id: circuitId, message, focus_component: focus ?? null })),
  previewUrl: (path: string) => `${BASE}${path}`,
};
