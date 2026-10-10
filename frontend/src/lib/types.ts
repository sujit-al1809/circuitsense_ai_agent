// Mirrors backend/app/schemas/*.py. Keep in sync with the API.

export type Provenance =
  | "image_inference"
  | "parsed_netlist"
  | "sample_annotation"
  | "datasheet"
  | "simulation"
  | "calculation"
  | "user_measurement";

export type ConnectivityStatus = "verified" | "inferred" | "unavailable";
export type ProviderMode = "demo" | "bedrock";

export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
  page: number;
  source: "sample_annotation" | "image_inference";
}

export interface ComponentPin {
  pin: string;
  name?: string | null;
  net?: string | null;
}

export interface CircuitComponent {
  ref: string;
  type: string;
  value?: string | null;
  part_number?: string | null;
  description?: string | null;
  confidence: number;
  provenance: Provenance;
  pins: ComponentPin[];
  bbox?: BoundingBox | null;
}

export interface NetNode {
  ref: string;
  pin: string;
  pin_name?: string | null;
}

export interface Net {
  name: string;
  nodes: NetNode[];
  verification: "verified" | "inferred";
  provenance: Provenance;
  confidence: number;
}

export interface Evidence {
  kind: Provenance;
  detail: string;
  source?: string | null;
}

export interface PotentialIssue {
  id: string;
  title: string;
  severity: "info" | "warning" | "critical";
  status: "confirmed" | "hypothesis";
  description: string;
  evidence: Evidence[];
  related_components: string[];
}

export interface DiagnosticAction {
  action: string;
  reason: string;
  target?: string | null;
  expected?: string | null;
  safety_note?: string | null;
}

export interface Uncertainty {
  topic: string;
  detail: string;
}

export interface QuantityValue {
  value: number;
  unit: string;
  display: string;
}

export interface CalculationResult {
  calculation: string;
  formula: string;
  inputs: Record<string, QuantityValue>;
  outputs: Record<string, QuantityValue>;
  assumptions: string[];
  steps: string[];
  warnings: string[];
}

export interface PreviewInfo {
  url: string;
  width: number;
  height: number;
  page: number;
  page_count: number;
}

export interface CircuitAnalysis {
  circuit_id: string;
  created_at: string;
  mode: ProviderMode;
  source_kind: "image" | "pdf" | "netlist" | "sample";
  source_filename?: string | null;
  sample_id?: string | null;
  title: string;
  circuit_type: string;
  summary: string;
  expected_behavior?: string | null;
  components: CircuitComponent[];
  nets: Net[];
  connectivity_status: ConnectivityStatus;
  provenance: Provenance[];
  uncertainties: Uncertainty[];
  issues: PotentialIssue[];
  calculations: CalculationResult[];
  component_roles: Record<string, string>;
  next_action?: DiagnosticAction | null;
  preview?: PreviewInfo | null;
  notices: string[];
}

export type Quantity = "voltage" | "current" | "resistance" | "frequency";

export interface MeasurementCreate {
  quantity: Quantity;
  value: number;
  unit: string;
  target: string;
  reference?: string | null;
  power_state: "powered" | "unpowered" | "unknown";
  notes?: string | null;
}

export interface Measurement extends MeasurementCreate {
  id: string;
  circuit_id: string;
  recorded_at: string;
  si_value: number;
  si_unit: string;
  source: "user";
}

export interface Citation {
  id: string;
  source: string;
  title: string;
  page?: number | null;
  section?: string | null;
  excerpt: string;
  illustrative: boolean;
  score: number;
}

export interface Finding {
  statement: string;
  basis: Provenance;
  detail?: string | null;
}

export interface ToolTrace {
  name: string;
  input: Record<string, unknown>;
  status: "ok" | "error" | "unavailable";
  summary: string;
  duration_ms: number;
}

export interface ChatResponse {
  message_id: string;
  circuit_id: string;
  created_at: string;
  mode: ProviderMode;
  answer: string;
  confirmed: Finding[];
  hypotheses: Finding[];
  next_step?: DiagnosticAction | null;
  follow_up_questions: string[];
  citations: Citation[];
  calculations: CalculationResult[];
  measurements_recorded: Measurement[];
  tool_calls: ToolTrace[];
  iterations: number;
  stopped_reason: "final" | "max_iterations" | "tool_failures" | "provider_error";
  focus_component?: string | null;
  warnings: string[];
  notice?: string | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  focus_component?: string | null;
  response?: ChatResponse | null;
}

export interface CircuitSession {
  analysis: CircuitAnalysis;
  messages: ChatMessage[];
  measurements: Measurement[];
}

export interface TracedConnection {
  ref: string;
  pin: string;
  pin_name?: string | null;
  component_type?: string | null;
  value?: string | null;
}

export interface TracedNet {
  net: string;
  via_pin?: string | null;
  via_pin_name?: string | null;
  connections: TracedConnection[];
}

export interface TraceResult {
  target: string;
  kind: "component" | "pin" | "net" | "unknown";
  verified: boolean;
  provenance?: Provenance | null;
  nets: TracedNet[];
  path?: string[] | null;
  path_description?: string | null;
  uncertainty?: string | null;
  inferred_hint: TracedNet[];
}

export interface ProviderInfo {
  mode: ProviderMode;
  label: string;
  configured: boolean;
  region?: string | null;
  text_model_id?: string | null;
  vision_model_id?: string | null;
  issues: string[];
  notes: string[];
}

export interface Health {
  status: "ok" | "degraded";
  app: string;
  version: string;
  provider: ProviderInfo;
  simulation_available: boolean;
  datasheet_documents: number;
  sample_circuits: number;
  storage: string;
}

export interface SampleCircuitInfo {
  id: string;
  name: string;
  description: string;
  circuit_type: string;
  example_questions: string[];
}
