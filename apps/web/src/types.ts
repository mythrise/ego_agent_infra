export const STAGES = [
  "INTAKE",
  "CONTEXT",
  "PLAN",
  "PLAN_REVIEW",
  "APPROVAL",
  "EXECUTE",
  "OBSERVE",
  "EVALUATE",
  "VERIFY",
  "DECIDE",
  "ARCHIVE",
  "MEMORY_SKILL",
  "COMPLETED",
] as const;

export type ResearchStage = (typeof STAGES)[number];
export type RunStatus = "queued" | "running" | "passed" | "failed" | "blocked";

export interface AcceptanceMetric {
  key: string;
  label: string;
  operator: string;
  target: number | string;
  unit?: string;
}

export interface Experiment {
  id: string;
  name: string;
  variant: string;
  status: RunStatus;
  fps?: number;
  mpjpe?: number;
  latency?: number;
  vram?: number;
  gpuLane?: string;
  manifestDigest?: string;
  seeds?: number;
}

export interface EvidenceItem {
  id: string;
  kind: string;
  label: string;
  status: "verified" | "present" | "missing" | "pending";
  digest?: string;
  source?: string;
  verifiedBy?: string;
  createdAt?: string;
  raw?: Record<string, unknown>;
}

export interface TraceEvent {
  id: string;
  at: string;
  agent: string;
  kind: "agent" | "skill" | "mcp" | "control";
  target: string;
  status: "ok" | "running" | "blocked" | "queued";
  message: string;
  durationMs?: number;
}

export interface ResourceSnapshot {
  label: string;
  value: number;
  unit: "%" | "GB" | "ms";
  series: number[];
  note?: string;
}

export interface ApprovalGate {
  id: string;
  status: "pending" | "approved" | "rejected";
  riskLevel: string;
  summary: string;
  expectedDigest: string;
  requestedBy: string;
  estimatedGpuHours?: number;
  rollbackPoint?: string;
}

export interface MemoryProposal {
  title: string;
  observation: string;
  candidateSkill: string;
  version: string;
  status: "candidate" | "review" | "promoted";
  supportCount: number;
}

export interface IntegrationTruth {
  id: string;
  name: string;
  category: string;
  status: "connected" | "simulated" | "unconfigured" | "disabled";
  mode: string;
  detail: string;
  verifiedAt?: string;
}

export interface ResearchTask {
  id: string;
  generation: string;
  title: string;
  objective: string;
  stage: ResearchStage;
  status: string;
  riskLevel: string;
  updatedAt: string;
  acceptance: AcceptanceMetric[];
  experiments: Experiment[];
  evidence: EvidenceItem[];
  trace: TraceEvent[];
  resources: ResourceSnapshot[];
  pendingApproval?: ApprovalGate;
  memoryProposal?: MemoryProposal;
  evidenceRequired: number;
  evidencePresent: number;
  gateStatus: "not_run" | "pass" | "fail";
  decision?: "KEEP" | "REVERT" | "ITERATE" | "INCONCLUSIVE";
}

export interface DashboardData {
  tasks: ResearchTask[];
  activeTaskId: string;
  integrations: IntegrationTruth[];
  demoMode: boolean;
  runtimeMode: "local_api" | "static_replay";
  generatedAt: string;
}

export interface RXPCell {
  cellId: string;
  state: string;
  determinismLevel: string;
  intentDigest: string;
  grantDigest?: string;
  receiptDigest?: string;
  decisionDigest?: string;
  evidenceCount: number;
}

export interface RXPProtocolData {
  protocol: string;
  executionClass: string;
  physicalGpuRun: boolean;
  productionSignatureTrust: boolean;
  fixtureSignatureVerified: boolean;
  structuralVerification: "PASS" | "FAIL" | "NOT_RUN";
  verificationNotice: string;
  matrixId: string;
  completeness: "COMPLETE" | "INCOMPLETE";
  expectedCellCount: number;
  decidedCellCount: number;
  missingDecisions: string[];
  entryCount: number;
  root: string;
  canonicalSha256: string;
  cells: RXPCell[];
}

export interface DecisionRequest {
  decision: "approved" | "denied";
  expected_digest: string;
}

export type ExpertRole = "research-pi" | "scout" | "experiment-architect" | "reviewer";
export type ExpertRunStatus = "queued" | "running" | "completed" | "rejected" | "failed";

export interface ExpertModelReceipt {
  schema?: string;
  truth_boundary?: string;
  role?: string;
  model?: string;
  response_id?: string | null;
  http_status?: number;
  request_sha256?: string;
  response_sha256?: string;
  latency_ms?: number;
  usage?: Record<string, unknown>;
  attempt?: number;
}

export interface ExpertMemoryReceipt {
  truth_class?: string;
  receipt_sha256?: string;
  markdown_sha256?: string;
  raw_context_chars?: number;
  focus_chars?: number;
  compacted?: boolean;
}

export interface ExpertRoleState {
  role: ExpertRole;
  status: "queued" | "running" | "completed" | "failed";
  context_receipt?: {
    payload_sha256: string;
    payload_fields: string[];
    upstream_roles: ExpertRole[];
    input_sha256: string;
  } | null;
  output: Record<string, unknown> | null;
  receipt: ExpertModelReceipt | null;
  memory_receipt?: ExpertMemoryReceipt | null;
  error?: string | null;
}

export interface ExpertRunEvent {
  sequence: number;
  event_type: string;
  role: ExpertRole | null;
  status: string;
  message: string;
  details: Record<string, unknown>;
  created_at: string;
  previous_hash: string;
  event_hash: string;
}

export interface ExpertRun {
  schema: "egoagentos.live-expert-run/v1";
  run_id: string;
  status: ExpertRunStatus;
  created_at: string;
  updated_at: string;
  input: {
    mode: "detailed" | "idea" | "baseline";
    content: string;
    locale: "en" | "zh-CN";
    sha256: string;
  };
  provider: {
    configured: boolean;
    model: string | null;
    provider: string;
    credential_location: string;
    truth_boundary: string;
  };
  roles: ExpertRoleState[];
  events: ExpertRunEvent[];
  compile: {
    compile_sha256: string;
    tree_sha256: string;
    matrix_sha256: string;
    matrix_cell_count: number;
    tier: string;
    tree_children: string[];
    next_gate: string;
    resource_review: Record<string, unknown>;
  } | null;
  decision: {
    status: string;
    reviewer_verdict?: "PASS" | "WARN" | "FAIL";
    reviewed_digest?: string;
    execution_started: boolean;
    error?: string;
  } | null;
  truth_boundary: Record<string, string>;
  event_chain_sha256?: string;
  event_chain_valid?: boolean;
}

export interface StartExpertRunRequest {
  input_mode: "detailed" | "idea" | "baseline";
  content: string;
  locale: "en" | "zh-CN";
}
