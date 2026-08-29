import { syntheticTask } from "./demoData";
import { syntheticRXP } from "./rxpDemoData";
import { createStaticReplayApi } from "./staticReplay";
import { STAGES } from "./types";
import type {
  AcceptanceMetric,
  ApprovalGate,
  DashboardData,
  DecisionRequest,
  EvidenceItem,
  Experiment,
  IntegrationTruth,
  ResearchStage,
  ResearchTask,
  RXPProtocolData,
  ResourceSnapshot,
  TraceEvent,
} from "./types";

const API_ROOT = import.meta.env.VITE_API_ROOT ?? "/api/v1";
const FORCE_STATIC_REPLAY = import.meta.env.VITE_STATIC_DEMO === "true";
const APPROVAL_TOKEN_HEADER = "X-Ego-Approval-Token";
const MIN_OPERATOR_KEY_BYTES = 32;
const MAX_OPERATOR_KEY_BYTES = 4096;

// Deliberately module-memory only. The operator key must never be persisted,
// placed in a URL, or compiled into the frontend bundle.
let operatorSessionKey: string | undefined;

export function connectOperatorSession(key: string): void {
  const byteLength = new TextEncoder().encode(key).length;
  if (byteLength < MIN_OPERATOR_KEY_BYTES || byteLength > MAX_OPERATOR_KEY_BYTES) {
    throw new Error(
      `Operator key must contain ${MIN_OPERATOR_KEY_BYTES}-${MAX_OPERATOR_KEY_BYTES} UTF-8 bytes.`,
    );
  }
  operatorSessionKey = key;
}

export function clearOperatorSession(): void {
  operatorSessionKey = undefined;
}

export function operatorSessionConnected(): boolean {
  return operatorSessionKey !== undefined;
}

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function taskEventStreamUrl(taskId: string): string {
  return `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/event-stream`;
}

async function checkedResponse(path: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (operatorSessionKey) headers.set("Authorization", `Bearer ${operatorSessionKey}`);
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers,
  });

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as {
        detail?: string;
        message?: string;
        error?: { message?: string; request_id?: string };
      };
      detail = body.error?.message ?? body.detail ?? body.message ?? detail;
    } catch {
      // Keep the protocol-level error when a response has no JSON body.
    }
    throw new ApiError(response.status, detail);
  }

  return response;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await checkedResponse(path, init);

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function number(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return undefined;
}

function normalizeStage(value: unknown): ResearchStage {
  const candidate = text(value, "INTAKE").toUpperCase();
  return (STAGES as readonly string[]).includes(candidate) ? (candidate as ResearchStage) : "INTAKE";
}

function normalizeAcceptance(value: unknown): AcceptanceMetric[] {
  if (Array.isArray(value)) {
    return value.map((item, index) => {
      const row = record(item);
      const rule = text(row.rule).toLowerCase();
      const rawTarget = number(row.target ?? row.threshold);
      const relativePercent = rule.includes("relative") && rawTarget !== undefined && rawTarget <= 1;
      return {
        key: text(row.key ?? row.metric ?? row.name, `metric-${index}`),
        label: text(row.label ?? row.metric ?? row.name, `Metric ${index + 1}`),
        operator: text(row.operator, text(row.direction) === "lower_better" ? "≤" : "≥"),
        target: relativePercent ? rawTarget * 100 : rawTarget ?? text(row.target ?? row.threshold, "—"),
        unit: relativePercent ? "%" : text(row.unit) || undefined,
      };
    });
  }

  const map = record(value);
  return Object.entries(map).map(([key, target]) => ({
    key,
    label: key.replaceAll("_", " "),
    operator: key.includes("degradation") ? "≤" : "≥",
    target: number(target) ?? text(target, "—"),
  }));
}

function normalizeExperiments(value: unknown): Experiment[] {
  return array(value).map((item, index) => {
    const row = record(item);
    const metrics = record(row.metrics);
    const rawStatus = text(row.status, "queued").toLowerCase();
    const status = (["queued", "running", "passed", "failed", "blocked"] as const).includes(rawStatus as Experiment["status"])
      ? (rawStatus as Experiment["status"])
      : "queued";
    return {
      id: text(row.id ?? row.experiment_id, `exp-${index}`),
      name: text(row.name, `Arm ${index + 1}`),
      variant: text(row.variant ?? row.backbone ?? row.description ?? row.name, "Unspecified variant"),
      status,
      fps: number(row.fps ?? metrics.fps),
      mpjpe: number(row.mpjpe ?? metrics.mpjpe),
      latency: number(row.latency ?? row.latency_ms ?? metrics.latency),
      vram: number(row.vram ?? row.vram_gb ?? metrics.vram),
      gpuLane: text(row.gpu_lane ?? row.gpu_ids) || undefined,
      manifestDigest: text(row.manifest_digest ?? row.digest) || undefined,
      seeds: number(row.seeds ?? row.seed_count),
    };
  });
}

function normalizeEvidence(value: unknown): EvidenceItem[] {
  return array(value).map((item, index) => {
    const row = record(item);
    const rawStatus = text(row.status, "present").toLowerCase();
    const status = (["verified", "present", "missing", "pending"] as const).includes(rawStatus as EvidenceItem["status"])
      ? (rawStatus as EvidenceItem["status"])
      : "present";
    return {
      id: text(row.id ?? row.evidence_id, `evidence-${index}`),
      kind: text(row.kind ?? row.type, "artifact"),
      label: text(row.label ?? row.name ?? row.kind, "Evidence artifact"),
      status,
      digest: text(row.digest ?? row.sha256 ?? row.artifact_sha256 ?? row.artifact_digest) || undefined,
      source: text(row.source ?? row.producer ?? row.producer_id) || undefined,
      verifiedBy: text(row.verified_by ?? row.reviewer) || undefined,
      createdAt: text(row.created_at ?? row.timestamp) || undefined,
      raw: record(row.payload),
    };
  });
}

function normalizeTrace(value: unknown): TraceEvent[] {
  return array(value).map((item, index) => {
    const row = record(item);
    const rawKind = text(row.kind ?? row.type, "agent").toLowerCase();
    const kind = (["agent", "skill", "mcp", "control"] as const).includes(rawKind as TraceEvent["kind"])
      ? (rawKind as TraceEvent["kind"])
      : "agent";
    const rawStatus = text(row.status, "ok").toLowerCase();
    const status = (["ok", "running", "blocked", "queued"] as const).includes(rawStatus as TraceEvent["status"])
      ? (rawStatus as TraceEvent["status"])
      : "ok";
    return {
      id: text(row.id ?? row.span_id, `trace-${index}`),
      at: text(row.at ?? row.timestamp ?? row.created_at, "—"),
      agent: text(row.agent ?? row.actor, "Control Plane"),
      kind,
      target: text(row.target ?? row.operation ?? row.event_type ?? row.name, "operation"),
      status,
      message: text(row.message ?? row.detail, text(record(row.payload).notice, "Trace event recorded.")),
      durationMs: number(row.duration_ms),
    };
  });
}

function normalizeResources(value: unknown): ResourceSnapshot[] {
  return array(value).map((item, index) => {
    const row = record(item);
    const unitRaw = text(row.unit, "%");
    const unit: ResourceSnapshot["unit"] = unitRaw === "GB" || unitRaw === "ms" ? unitRaw : "%";
    return {
      label: text(row.label ?? row.name, `Resource ${index + 1}`),
      value: number(row.value) ?? 0,
      unit,
      series: array(row.series ?? row.values).map((point) => number(point) ?? 0),
      note: text(row.note) || undefined,
    };
  });
}

function normalizeApproval(value: unknown): ApprovalGate | undefined {
  const row = record(value);
  if (!Object.keys(row).length) return undefined;
  const statusRaw = text(row.status, "pending").toLowerCase();
  const status: ApprovalGate["status"] = statusRaw === "approved" || statusRaw === "consumed"
    ? "approved"
    : statusRaw === "rejected" || statusRaw === "denied" || statusRaw === "expired"
      ? "rejected"
      : "pending";
  return {
    id: text(row.id ?? row.approval_id, "approval-pending"),
    status,
    riskLevel: text(row.risk_level, "R2"),
    summary: text(row.summary ?? row.reason ?? row.action, "Bounded action requires review."),
    expectedDigest: text(row.expected_digest ?? row.digest ?? row.action_digest),
    requestedBy: text(row.requested_by ?? row.requester, "Control Plane"),
    estimatedGpuHours: number(row.estimated_gpu_hours ?? row.gpu_hours),
    rollbackPoint: text(row.rollback_point) || undefined,
  };
}

export function normalizeTask(value: unknown): ResearchTask {
  const row = record(value);
  const evidence = normalizeEvidence(row.evidence ?? row.evidence_items ?? row.ledger);
  const evidenceSummary = record(row.evidence_summary ?? row.evidence_gate);
  const memories = array(row.memories);
  const memory = record(row.memory_proposal ?? row.skill_candidate ?? memories.find((item) => record(item).memory_type === "procedural"));
  const fallback = syntheticTask;
  const goal = record(row.goal);
  const experiments = normalizeExperiments(row.experiments ?? row.experiment_matrix ?? row.runs ?? goal.candidate_arms);
  const evaluations = array(row.latest_evaluation);
  for (const evaluationValue of evaluations) {
    const evaluation = record(evaluationValue);
    const metric = text(evaluation.metric).toLowerCase();
    if (experiments[0]) {
      if (metric === "fps") experiments[0].fps = number(evaluation.baseline_mean);
      if (metric === "mpjpe") experiments[0].mpjpe = number(evaluation.baseline_mean);
    }
    if (experiments[1]) {
      if (metric === "fps") experiments[1].fps = number(evaluation.candidate_mean);
      if (metric === "mpjpe") experiments[1].mpjpe = number(evaluation.candidate_mean);
      experiments[1].status = text(evaluation.verdict).toLowerCase() === "pass" ? "passed" : "failed";
    }
  }
  if (evaluations.length && experiments[0]) experiments[0].status = "passed";
  const logEvidence = array(row.evidence ?? row.evidence_items ?? row.ledger)
    .map(record)
    .find((item) => text(item.kind) === "log");
  const logRecords = array(record(logEvidence?.payload).records).map(record);
  const derivedResources: ResourceSnapshot[] = logRecords.length
    ? [
        {
          label: "GPU utilization",
          value: number(logRecords.at(-1)?.gpu_util_pct) ?? 0,
          unit: "%",
          series: logRecords.map((item) => number(item.gpu_util_pct) ?? 0),
          note: "synthetic API payload",
        },
        {
          label: "CPU utilization",
          value: number(logRecords.at(-1)?.cpu_util_pct) ?? 0,
          unit: "%",
          series: logRecords.map((item) => number(item.cpu_util_pct) ?? 0),
          note: "synthetic API payload",
        },
      ]
    : [];
  const requiredCount = Array.isArray(evidenceSummary.required)
    ? evidenceSummary.required.length
    : number(row.evidence_required ?? evidenceSummary.required) ?? 8;
  const presentCount = Array.isArray(evidenceSummary.present)
    ? evidenceSummary.present.length
    : number(row.evidence_present ?? evidenceSummary.present) ?? evidence.filter((item) => item.status === "verified" || item.status === "present").length;
  const pendingApproval = normalizeApproval(row.pending_approval ?? row.approval);
  if (pendingApproval && pendingApproval.estimatedGpuHours === undefined) {
    pendingApproval.estimatedGpuHours = number(record(goal.constraints).compute_budget_gpu_hours);
  }
  if (text(record(row.gate_result).status).toLowerCase() === "pass") {
    evidence.forEach((item) => { item.status = "verified"; });
  }
  if (experiments[1] && text(row.run_manifest_digest)) {
    experiments[1].manifestDigest = text(row.run_manifest_digest);
  }
  return {
    id: text(row.id ?? row.task_id, fallback.id),
    generation: text(row.generation, fallback.generation),
    title: text(row.title ?? row.name, fallback.title),
    objective: text(row.objective ?? goal.objective, fallback.objective),
    stage: normalizeStage(row.stage ?? row.current_stage),
    status: text(row.status, "ACTIVE"),
    riskLevel: text(row.risk_level ?? row.risk, "R0"),
    updatedAt: text(row.updated_at ?? row.generated_at, new Date().toISOString()),
    acceptance: normalizeAcceptance(row.acceptance ?? row.acceptance_thresholds ?? goal.acceptance_metrics),
    experiments,
    evidence,
    trace: normalizeTrace(row.trace ?? row.trace_events ?? row.events ?? row.spans),
    resources: normalizeResources(row.resources ?? row.resource_trace ?? row.telemetry).length
      ? normalizeResources(row.resources ?? row.resource_trace ?? row.telemetry)
      : derivedResources,
    pendingApproval,
    memoryProposal: Object.keys(memory).length
      ? {
          title: text(memory.title ?? memory.statement, "Procedure candidate"),
          observation: text(memory.observation ?? memory.description ?? memory.statement, "Awaiting independent evidence."),
          candidateSkill: text(memory.candidate_skill ?? memory.skill ?? memory.component, "not assigned"),
          version: text(memory.version, "0.1.0-candidate"),
          status: text(memory.status, "candidate") as "candidate" | "review" | "promoted",
          supportCount: number(memory.support_count) ?? (memory.validated === true ? 1 : 0),
        }
      : undefined,
    evidenceRequired: requiredCount,
    evidencePresent: presentCount,
    gateStatus: (["not_run", "pass", "fail"] as const).includes(
      text(record(row.gate_result).status ?? evidenceSummary.gate_status, "not_run") as ResearchTask["gateStatus"],
    )
      ? (text(record(row.gate_result).status ?? evidenceSummary.gate_status, "not_run") as ResearchTask["gateStatus"])
      : "not_run",
    decision: (["KEEP", "REVERT", "ITERATE", "INCONCLUSIVE"] as const).includes(
      text(row.decision).toUpperCase() as NonNullable<ResearchTask["decision"]>,
    )
      ? (text(row.decision).toUpperCase() as NonNullable<ResearchTask["decision"]>)
      : undefined,
  };
}

function normalizeIntegration(value: unknown, index: number): IntegrationTruth {
  const row = record(value);
  const rawStatus = text(row.status, "unconfigured").toLowerCase();
  const status: IntegrationTruth["status"] = rawStatus === "ready" || rawStatus === "connected"
    ? "connected"
    : rawStatus === "simulated"
      ? "simulated"
      : rawStatus === "unavailable" || rawStatus === "disabled"
        ? "disabled"
        : "unconfigured";
  return {
    id: text(row.id, `integration-${index}`),
    name: text(row.name, "Integration"),
    category: text(row.category ?? row.kind ?? row.role, "External"),
    status,
    mode: text(row.mode, rawStatus.replaceAll("_", " ")),
    detail: text(row.detail ?? row.message, "No connection claim supplied."),
    verifiedAt: text(row.verified_at ?? row.checked_at) || undefined,
  };
}

export function normalizeRXP(value: unknown): RXPProtocolData {
  const row = record(value);
  const ledger = record(row.ledger ?? row);
  return {
    protocol: text(row.protocol, "RXP/1.0"),
    executionClass: text(row.execution_class, "unknown"),
    physicalGpuRun: row.physical_gpu_run === true,
    productionSignatureTrust: row.production_signature_trust === true,
    fixtureSignatureVerified: row.fixture_signature_verified === true,
    structuralVerification: (["PASS", "FAIL", "NOT_RUN"] as const).includes(
      text(row.structural_verification, "NOT_RUN") as RXPProtocolData["structuralVerification"],
    )
      ? (text(row.structural_verification, "NOT_RUN") as RXPProtocolData["structuralVerification"])
      : "NOT_RUN",
    verificationNotice: text(
      row.fixture_key_notice,
      "No production issuer trust claim was supplied.",
    ),
    matrixId: text(ledger.matrix_id, "matrix:not-emitted"),
    completeness: text(ledger.completeness) === "COMPLETE" ? "COMPLETE" : "INCOMPLETE",
    expectedCellCount: number(ledger.expected_cell_count) ?? 0,
    decidedCellCount: number(ledger.decided_cell_count) ?? 0,
    missingDecisions: array(ledger.missing_decisions).map((item) => text(item, "unknown-cell")),
    entryCount: number(ledger.entry_count) ?? 0,
    root: text(ledger.root, "not-emitted"),
    canonicalSha256: text(row.canonical_sha256, "not-emitted"),
    cells: array(ledger.cells).map((item, index) => {
      const cell = record(item);
      return {
        cellId: text(cell.cell_id, `cell-${index}`),
        state: text(cell.state, "UNKNOWN"),
        determinismLevel: text(cell.determinism_level, "D0_UNVERIFIED"),
        intentDigest: text(cell.intent_digest, "not-emitted"),
        grantDigest: text(cell.grant_digest) || undefined,
        receiptDigest: text(cell.receipt_digest) || undefined,
        decisionDigest: text(cell.decision_digest) || undefined,
        evidenceCount: array(cell.evidence_digests).length,
      };
    }),
  };
}

export function normalizeDashboard(value: unknown, integrationsValue?: unknown): DashboardData {
  const row = record(value);
  const taskValues = array(row.tasks);
  if (!taskValues.length && (row.task || row.active_task)) taskValues.push(row.task ?? row.active_task);
  if (!taskValues.length && (row.id || row.task_id)) taskValues.push(row);
  const demo = record(row.demo);
  const activeTaskId = text(row.active_task_id ?? record(row.active_task).id ?? demo.task_id);
  const activeGeneration = text(demo.generation);
  const dashboardTrace = normalizeTrace(row.activity);
  const tasks = taskValues.map((taskValue) => {
    const task = normalizeTask(taskValue);
    if (task.id === activeTaskId && (!activeGeneration || task.generation === activeGeneration)) {
      task.trace = dashboardTrace;
    }
    return task;
  });
  const integrationContainer = record(integrationsValue ?? row.integrations);
  const integrations = array(integrationContainer.items ?? integrationsValue ?? row.integrations).map(normalizeIntegration);
  return {
    tasks,
    activeTaskId: activeTaskId || tasks[0]?.id || "",
    integrations,
    demoMode: row.demo_mode !== false && demo.synthetic !== false,
    runtimeMode: "local_api",
    generatedAt: text(row.generated_at ?? demo.generated_at, new Date().toISOString()),
  };
}

const backendApi = {
  async dashboard(): Promise<DashboardData> {
    const [dashboard, integrations] = await Promise.all([
      request<unknown>("/dashboard"),
      request<unknown>("/integrations").catch(() => undefined),
    ]);
    const normalized = normalizeDashboard(dashboard, integrations && (record(integrations).items ?? integrations));
    return normalized;
  },

  async task(id: string): Promise<ResearchTask> {
    return normalizeTask(await request<unknown>(`/tasks/${encodeURIComponent(id)}`));
  },

  async reset(): Promise<unknown> {
    return request("/demo/reset", { method: "POST", body: "{}" });
  },

  async advance(id: string, approvalToken?: string): Promise<unknown> {
    return request(`/tasks/${encodeURIComponent(id)}/advance`, {
      method: "POST",
      body: JSON.stringify({
        idempotency_key: crypto.randomUUID(),
        ...(approvalToken ? { approval_token: approvalToken } : {}),
      }),
    });
  },

  async autorun(id: string, approvalToken?: string): Promise<unknown> {
    return request(`/tasks/${encodeURIComponent(id)}/autorun`, {
      method: "POST",
      body: JSON.stringify({
        idempotency_key: crypto.randomUUID(),
        ...(approvalToken ? { approval_token: approvalToken } : {}),
      }),
    });
  },

  async decide(approvalId: string, payload: DecisionRequest): Promise<{ approval_token?: string }> {
    const response = await checkedResponse(`/approvals/${encodeURIComponent(approvalId)}/decision`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const body = (await response.json()) as { approval_token?: string | null };
    const headerToken = response.headers.get(APPROVAL_TOKEN_HEADER);
    return {
      ...body,
      approval_token: headerToken ?? body.approval_token ?? undefined,
    };
  },

  async rxpDemo(): Promise<RXPProtocolData> {
    return normalizeRXP(await request<unknown>("/rxp/demo"));
  },
};

export function createResearchApi(forceStaticReplay = FORCE_STATIC_REPLAY) {
  const staticReplay = createStaticReplayApi();
  let mode: "unknown" | "local_api" | "static_replay" = forceStaticReplay ? "static_replay" : "unknown";

  return {
    async dashboard(): Promise<DashboardData> {
      if (mode === "static_replay") return staticReplay.dashboard();
      try {
        const result = await backendApi.dashboard();
        mode = "local_api";
        return result;
      } catch (error) {
        if (mode === "local_api") throw error;
        mode = "static_replay";
        return staticReplay.dashboard();
      }
    },

    async task(id: string): Promise<ResearchTask> {
      return mode === "static_replay" ? staticReplay.task(id) : backendApi.task(id);
    },

    async reset(): Promise<unknown> {
      return mode === "static_replay" ? staticReplay.reset() : backendApi.reset();
    },

    async advance(id: string, approvalToken?: string): Promise<unknown> {
      return mode === "static_replay"
        ? staticReplay.advance(id, approvalToken)
        : backendApi.advance(id, approvalToken);
    },

    async autorun(id: string, approvalToken?: string): Promise<unknown> {
      return mode === "static_replay"
        ? staticReplay.autorun(id, approvalToken)
        : backendApi.autorun(id, approvalToken);
    },

    async decide(approvalId: string, payload: DecisionRequest): Promise<{ approval_token?: string }> {
      return mode === "static_replay"
        ? staticReplay.decide(approvalId, payload)
        : backendApi.decide(approvalId, payload);
    },

    async rxpDemo(): Promise<RXPProtocolData> {
      return mode === "static_replay" ? structuredClone(syntheticRXP) : backendApi.rxpDemo();
    },
  };
}

export const researchApi = createResearchApi();

export { ApiError };
