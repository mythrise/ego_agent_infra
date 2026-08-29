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
