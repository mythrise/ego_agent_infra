import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Activity,
  ArrowRight,
  BookOpen,
  Bot,
  Check,
  ChevronDown,
  CircleAlert,
  Code2,
  Database,
  FileCheck2,
  FlaskConical,
  Gauge,
  GitBranch,
  KeyRound,
  Menu,
  Network,
  Pause,
  Play,
  RefreshCcw,
  RotateCcw,
  ShieldCheck,
  SlidersHorizontal,
  TerminalSquare,
  Workflow,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  clearOperatorSession,
  connectOperatorSession,
  operatorSessionConnected,
  researchApi,
  taskEventStreamUrl,
} from "./api";
import { syntheticDashboard } from "./demoData";
import { syntheticRXP } from "./rxpDemoData";
import { STAGES } from "./types";
import type {
  ApprovalGate,
  DashboardData,
  EvidenceItem,
  Experiment,
  IntegrationTruth,
  ResearchTask,
  RXPProtocolData,
  ResourceSnapshot,
  TraceEvent,
} from "./types";

type BusyAction = "reset" | "advance" | "autorun" | "approve" | "reject" | null;
type SessionApprovalGrant = { token: string; generation: string } | null;

function approvalTokenForGeneration(
  grant: SessionApprovalGrant,
  generation: string,
): string | undefined {
  return grant?.generation === generation ? grant.token : undefined;
}

const navItems = [
  { id: "cockpit", label: "Task cockpit", icon: Gauge },
  { id: "acceptance", label: "Semifinal acceptance", icon: ShieldCheck },
  { id: "experiments", label: "Experiments", icon: FlaskConical },
  { id: "protocol", label: "RXP protocol", icon: KeyRound },
  { id: "evidence", label: "Evidence", icon: FileCheck2 },
  { id: "trace", label: "Audit trace", icon: Workflow },
  { id: "integrations", label: "Integrations", icon: Network },
];

const sourceLinks = [
  {
    label: "GitHub source",
    href: "https://github.com/mythrise/ego_agent_infra",
    icon: Code2,
  },
  {
    label: "Read the README",
    href: "https://github.com/mythrise/ego_agent_infra#readme",
    icon: BookOpen,
  },
  {
    label: "Third-party notices",
    href: `${import.meta.env.BASE_URL}THIRD_PARTY_NOTICES.txt`,
    icon: FileCheck2,
  },
];

function mergeTask(dashboard: DashboardData, task: ResearchTask): DashboardData {
  const existing = dashboard.tasks.findIndex((item) => item.id === task.id);
  const tasks = [...dashboard.tasks];
  if (existing >= 0) {
    const summary = tasks[existing];
    tasks[existing] = {
      ...task,
      trace: task.trace.length ? task.trace : summary.trace,
      resources: task.resources.length ? task.resources : summary.resources,
    };
  }
  else tasks.unshift(task);
  return { ...dashboard, tasks, activeTaskId: task.id };
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "unknown";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function formatMetric(value: number | undefined, suffix = ""): string {
  if (value === undefined) return "—";
  return `${Number.isInteger(value) ? value : value.toFixed(1)}${suffix}`;
}

function App() {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [rxp, setRxp] = useState<RXPProtocolData | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<EvidenceItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [approvalGrant, setApprovalGrant] = useState<SessionApprovalGrant>(null);
  const [operatorConnected, setOperatorConnected] = useState(operatorSessionConnected);
  const [navOpen, setNavOpen] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const prefersReducedMotion = useReducedMotion();
  const noticeTimer = useRef<number | undefined>(undefined);

  const activeTask = useMemo(() => {
    if (!dashboard) return undefined;
    return dashboard.tasks.find((task) => task.id === selectedTaskId) ?? dashboard.tasks[0];
  }, [dashboard, selectedTaskId]);
  const approvalToken = activeTask
    ? approvalTokenForGeneration(approvalGrant, activeTask.generation)
    : undefined;

  const showNotice = useCallback((message: string) => {
    window.clearTimeout(noticeTimer.current);
    setNotice(message);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 4200);
  }, []);

  const load = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true);
    else setLoading(true);
    try {
      const next = await researchApi.dashboard();
      const nextRXP = await researchApi.rxpDemo().catch(() => null);
      const taskId = selectedTaskId || next.activeTaskId || next.tasks[0]?.id;
      let hydrated = next;
      if (taskId) {
        try {
          hydrated = mergeTask(next, await researchApi.task(taskId));
        } catch {
          // The dashboard remains useful if the optional detail hydration fails.
        }
      }
      setDashboard(hydrated);
      setRxp(nextRXP);
      if (hydrated.runtimeMode === "static_replay") setAutoRefresh(false);
      setSelectedTaskId((current) => current || hydrated.activeTaskId || hydrated.tasks[0]?.id || "");
      setError(null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "The control plane did not return a dashboard.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [selectedTaskId]);

  useEffect(() => {
    void load();
    return () => window.clearTimeout(noticeTimer.current);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (
      !autoRefresh
      || !activeTask
      || dashboard?.runtimeMode === "static_replay"
    ) return;

    let fallbackInterval: number | undefined;
    let debounceTimer: number | undefined;
    let connectionTimer: number | undefined;
    const startFallback = () => {
      if (fallbackInterval !== undefined) return;
      // LISTEN/NOTIFY is a wake-up, while a sparse reconciliation remains the
      // durable safety net for proxies that cannot carry Server-Sent Events.
      fallbackInterval = window.setInterval(() => void load(true), 30_000);
    };
    if (!("EventSource" in window)) {
      startFallback();
      return () => window.clearInterval(fallbackInterval);
    }

    const source = new EventSource(taskEventStreamUrl(activeTask.id));
    connectionTimer = window.setTimeout(startFallback, 5_000);
    source.onopen = () => {
      window.clearTimeout(connectionTimer);
      if (fallbackInterval !== undefined) {
        window.clearInterval(fallbackInterval);
        fallbackInterval = undefined;
      }
    };
    source.onmessage = () => {
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(() => void load(true), 120);
    };
    source.onerror = () => startFallback();

    return () => {
      source.close();
      window.clearTimeout(connectionTimer);
      window.clearTimeout(debounceTimer);
      window.clearInterval(fallbackInterval);
    };
  }, [activeTask?.id, autoRefresh, dashboard?.runtimeMode, load]);

  const runAction = async (action: Exclude<BusyAction, null>, callback: () => Promise<unknown>, success: string) => {
    setBusy(action);
    try {
      await callback();
      showNotice(success);
      await load(true);
    } catch (actionError) {
      showNotice(actionError instanceof Error ? actionError.message : "Action failed.");
    } finally {
      setBusy(null);
    }
  };

  const decide = async (gate: ApprovalGate, decision: "approve" | "reject") => {
    setBusy(decision);
    try {
      const result = await researchApi.decide(gate.id, {
        decision: decision === "approve" ? "approved" : "denied",
        expected_digest: gate.expectedDigest,
      });
      if (result.approval_token && activeTask) {
        setApprovalGrant({ token: result.approval_token, generation: activeTask.generation });
      }
      if (decision === "reject") setApprovalGrant(null);
      showNotice(
        decision === "approve"
          ? dashboard?.runtimeMode === "static_replay"
            ? "Synthetic approval grant recorded in browser memory. No API, MCP, signature, or GPU action occurred."
            : "Approval recorded. The one-time token is held in this session only."
          : "Execution rejected; the decision was appended to the audit trail.",
      );
      await load(true);
    } catch (actionError) {
      showNotice(actionError instanceof Error ? actionError.message : "Decision failed.");
    } finally {
      setBusy(null);
    }
  };

  if (loading && !dashboard) return <LoadingScreen />;

  if (error && !dashboard) {
    return (
      <ErrorScreen
        message={error}
        onRetry={() => void load()}
        onFixture={() => {
          setDashboard(syntheticDashboard);
          setRxp(structuredClone(syntheticRXP));
          setSelectedTaskId(syntheticDashboard.activeTaskId);
          setError(null);
          setAutoRefresh(false);
        }}
      />
    );
  }

  if (!dashboard || !activeTask) {
    return <EmptyScreen onReset={() => void runAction("reset", researchApi.reset, "Synthetic demo task restored.")} />;
  }

  return (
    <div className="app-shell">
      <NoiseField />
      <Rail open={navOpen} onClose={() => setNavOpen(false)} runtimeMode={dashboard.runtimeMode} />

      <main className="workspace" id="main-content">
        <Topbar
          task={activeTask}
          tasks={dashboard.tasks}
          selectedTaskId={selectedTaskId}
          onTaskChange={setSelectedTaskId}
          onMenu={() => setNavOpen(true)}
          refreshing={refreshing}
          autoRefresh={autoRefresh}
          onToggleAuto={() => setAutoRefresh((value) => !value)}
          onRefresh={() => void load(true)}
          runtimeMode={dashboard.runtimeMode}
        />
        {dashboard.runtimeMode === "local_api" && (
          <OperatorSessionBar
            connected={operatorConnected}
            onConnect={(key) => {
              try {
                connectOperatorSession(key);
                setOperatorConnected(true);
                showNotice("Operator session connected in memory only.");
                return true;
              } catch (sessionError) {
                showNotice(sessionError instanceof Error ? sessionError.message : "Operator session rejected.");
                return false;
              }
            }}
            onClear={() => {
              clearOperatorSession();
              setOperatorConnected(false);
              setApprovalGrant(null);
              showNotice("Operator session cleared from memory.");
            }}
          />
        )}

        <div className="workspace-grid">
          <div className="primary-column">
            <TaskCommand task={activeTask} runtimeMode={dashboard.runtimeMode} />
            <RXPProtocolView data={rxp} runtimeMode={dashboard.runtimeMode} />
            <AcceptanceReadiness runtimeMode={dashboard.runtimeMode} />
            <StageSpine current={activeTask.stage} reducedMotion={Boolean(prefersReducedMotion)} />

            <div className="operating-grid">
              <ExperimentMatrix experiments={activeTask.experiments} />
              <ResourceTrace resources={activeTask.resources} trace={activeTask.trace} />
            </div>

            {activeTask.stage === "APPROVAL" && activeTask.pendingApproval?.status === "pending" && (
              <ApprovalPanel
                gate={activeTask.pendingApproval}
                busy={busy}
                operatorReady={dashboard.runtimeMode === "static_replay" || operatorConnected}
                onDecision={(decision) => void decide(activeTask.pendingApproval!, decision)}
              />
            )}

            <EvidenceLedger
              evidence={activeTask.evidence}
              present={activeTask.evidencePresent}
              required={activeTask.evidenceRequired}
              gateStatus={activeTask.gateStatus}
              onInspect={setSelectedEvidence}
            />
            <MetricComparison experiments={activeTask.experiments} />
          </div>

          <Inspector
            task={activeTask}
            integrations={dashboard.integrations}
            selectedEvidence={selectedEvidence}
            onCloseEvidence={() => setSelectedEvidence(null)}
          />
        </div>
      </main>

      <ActionDock
        task={activeTask}
        busy={busy}
        approvalToken={approvalToken}
        onReset={() =>
          void runAction(
            "reset",
            async () => {
              const result = await researchApi.reset();
              setApprovalGrant(null);
              return result;
            },
            "Synthetic demo reset to a clean state.",
          )
        }
        onAdvance={() =>
          void runAction(
            "advance",
            async () => {
              const result = await researchApi.advance(activeTask.id, approvalToken);
              if (approvalToken) setApprovalGrant(null);
              return result;
            },
            dashboard.runtimeMode === "static_replay"
              ? "Synthetic state transition replayed in browser memory."
              : "Deterministic state transition requested.",
          )
        }
        onAutorun={() =>
          void runAction(
            "autorun",
            async () => {
              const result = await researchApi.autorun(activeTask.id, approvalToken);
              if (approvalToken) setApprovalGrant(null);
              return result;
            },
            dashboard.runtimeMode === "static_replay"
              ? "Synthetic browser replay continued to the next policy or evidence gate."
              : "Autorun continued to the next policy or evidence gate.",
          )
        }
        runtimeMode={dashboard.runtimeMode}
        operatorConnected={operatorConnected}
      />

      <AnimatePresence>
        {notice && (
          <motion.div
            className="toast"
            role="status"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
          >
            <span className="status-dot positive" />
            {notice}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function OperatorSessionBar({
  connected,
  onConnect,
  onClear,
}: {
  connected: boolean;
  onConnect: (key: string) => boolean;
  onClear: () => void;
}) {
  const [key, setKey] = useState("");

  return (
    <section className="operator-strip" aria-label="Operator session">
      <div className={`operator-state ${connected ? "connected" : "locked"}`}>
        <KeyRound size={13} aria-hidden="true" />
        <span>{connected ? "OPERATOR CONNECTED" : "MUTATIONS LOCKED"}</span>
        <small>{connected ? "MEMORY ONLY · CLEAR ON TAB RELOAD" : "BEARER KEY REQUIRED"}</small>
      </div>
      {connected ? (
        <button className="operator-clear" type="button" onClick={onClear}>
          Clear session
        </button>
      ) : (
        <form
          className="operator-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (onConnect(key)) setKey("");
          }}
        >
          <label htmlFor="operator-session-key">Session operator key</label>
          <input
            id="operator-session-key"
            aria-label="Operator session key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={key}
            onChange={(event) => setKey(event.target.value)}
            placeholder="Paste deployment key"
          />
          <button type="submit" disabled={!key}>Connect session</button>
        </form>
      )}
    </section>
  );
}

function NoiseField() {
  return <div className="noise-field" aria-hidden="true" />;
}

function Rail({
  open,
  onClose,
  runtimeMode,
}: {
  open: boolean;
  onClose: () => void;
  runtimeMode: DashboardData["runtimeMode"];
}) {
  return (
    <>
      {open && <button className="nav-scrim" aria-label="Close navigation" onClick={onClose} />}
      <aside className={`rail ${open ? "is-open" : ""}`} aria-label="Primary navigation">
        <a className="brand-mark" href="#cockpit" aria-label="EgoAgentOS ResearchOps home" onClick={onClose}>
          <span className="brand-glyph">E</span>
          <span className="brand-type">EgoAgentOS</span>
        </a>
        <nav className="rail-nav">
          {navItems.map(({ id, label, icon: Icon }, index) => (
            <a
              href={`#${id}`}
              className={index === 0 ? "active" : ""}
              key={id}
              aria-current={index === 0 ? "page" : undefined}
              onClick={onClose}
            >
              <Icon size={17} strokeWidth={1.6} aria-hidden="true" />
              <span>{label}</span>
            </a>
          ))}
          <span className="rail-divider" aria-hidden="true" />
          {sourceLinks.map(({ label, href, icon: Icon }) => (
            <a href={href} key={label} target="_blank" rel="noreferrer" onClick={onClose}>
              <Icon size={17} strokeWidth={1.6} aria-hidden="true" />
              <span>{label}</span>
            </a>
          ))}
        </nav>
        <div className="rail-footer">
          <span className="mode-light" aria-hidden="true" />
          <span>{runtimeMode === "static_replay" ? <>Browser replay<br />no live services</> : <>Synthetic<br />API workspace</>}</span>
        </div>
      </aside>
    </>
  );
}

function Topbar({
  task,
  tasks,
  selectedTaskId,
  onTaskChange,
  onMenu,
  refreshing,
  autoRefresh,
  onToggleAuto,
  onRefresh,
  runtimeMode,
}: {
  task: ResearchTask;
  tasks: ResearchTask[];
  selectedTaskId: string;
  onTaskChange: (id: string) => void;
  onMenu: () => void;
  refreshing: boolean;
  autoRefresh: boolean;
  onToggleAuto: () => void;
  onRefresh: () => void;
  runtimeMode: DashboardData["runtimeMode"];
}) {
  const staticReplay = runtimeMode === "static_replay";
  return (
    <header className="topbar">
      <button className="icon-button mobile-menu" onClick={onMenu} aria-label="Open navigation">
        <Menu size={18} />
      </button>
      <div className="context-line">
        <span className="eyebrow">RESEARCHOPS / TASK</span>
        <label className="task-picker">
          <span className="sr-only">Selected research task</span>
          <select value={selectedTaskId} onChange={(event) => onTaskChange(event.target.value)}>
            {tasks.map((item) => (
              <option value={item.id} key={item.id}>{item.id}</option>
            ))}
          </select>
          <ChevronDown size={13} aria-hidden="true" />
        </label>
      </div>
      <div className="topbar-meta">
        <span
          className="demo-stamp"
          title={staticReplay ? "Browser-only fixture: no API, MCP, AgentTeams, or GPU connection" : "Synthetic scenario served by the local API"}
        >
          <span className="demo-label-full">{staticReplay ? "SYNTHETIC · STATIC REPLAY · NO API/MCP" : "SYNTHETIC · LOCAL API"}</span>
          <span className="demo-label-mobile">{staticReplay ? "STATIC · SYNTHETIC" : "API · SYNTHETIC"}</span>
        </span>
        <span className="sync-time">UPDATED {formatTime(task.updatedAt)}</span>
        {!staticReplay && (
          <button
            className={`icon-button ${autoRefresh ? "is-active" : ""}`}
            onClick={onToggleAuto}
            aria-label={autoRefresh ? "Pause automatic refresh" : "Resume automatic refresh"}
            title={autoRefresh ? "Auto refresh on" : "Auto refresh paused"}
          >
            {autoRefresh ? <Pause size={14} /> : <Play size={14} />}
          </button>
        )}
        <button className="icon-button" onClick={onRefresh} aria-label="Refresh dashboard">
          <RefreshCcw className={refreshing ? "spin" : ""} size={14} />
        </button>
      </div>
    </header>
  );
}

function TaskCommand({ task, runtimeMode }: { task: ResearchTask; runtimeMode: DashboardData["runtimeMode"] }) {
  const staticReplay = runtimeMode === "static_replay";
  return (
    <section className="task-command" id="cockpit" aria-labelledby="task-title">
      <div className="command-main">
        <div className="section-kicker">
          <span className={`live-mark ${task.stage === "COMPLETED" || staticReplay ? "settled" : ""}`} />
          {staticReplay
            ? task.stage === "COMPLETED" ? "Completed synthetic replay" : "Synthetic replay checkpoint"
            : task.stage === "COMPLETED" ? "Completed research task" : "Active research goal"}
        </div>
        <h1 id="task-title">{task.title}</h1>
        <p>{task.objective}</p>
      </div>
      <div className="acceptance-strip" aria-label="Acceptance thresholds">
        {task.acceptance.length ? (
          task.acceptance.map((metric) => (
            <div className="acceptance-item" key={metric.key}>
              <span>{metric.label}</span>
              <strong>
                {metric.operator} {metric.target} <small>{metric.unit}</small>
              </strong>
            </div>
          ))
        ) : (
          <div className="acceptance-item empty-inline">
            <span>Acceptance schema</span>
            <strong>Not supplied</strong>
          </div>
        )}
        <div className="acceptance-item risk-item">
          <span>Policy class</span>
          <strong>{task.riskLevel} <small>approval bounded</small></strong>
        </div>
        {task.decision && (
          <div className="acceptance-item decision-item">
            <span>Verified decision</span>
            <strong>{task.decision} <small>gate bound</small></strong>
          </div>
        )}
      </div>
    </section>
  );
}

function compactDigest(value: string | undefined): string {
  if (!value || value === "not-emitted") return "not emitted";
  return value.length > 28 ? `${value.slice(0, 16)}…${value.slice(-8)}` : value;
}

function RXPProtocolView({
  data,
  runtimeMode,
}: {
  data: RXPProtocolData | null;
  runtimeMode: DashboardData["runtimeMode"];
}) {
  const [selectedCellId, setSelectedCellId] = useState("");
  const selectedCell = data?.cells.find((cell) => cell.cellId === selectedCellId) ?? data?.cells[0];
  const lifecycle = ["Intent", "Grant", "Receipt", "Evidence", "Decision"];

  return (
    <section className="rxp-section" id="protocol" aria-labelledby="rxp-title">
      <SectionHeading
        id="rxp-title"
        index="RXP/1"
        title="Research eXecution Protocol"
        note="Experiment authority becomes a replayable causal chain"
      />
      {data ? (
        <>
          <div className="rxp-truthline">
            <span className={`rxp-verdict ${data.structuralVerification.toLowerCase()}`}>
              <ShieldCheck size={14} /> STRUCTURE {data.structuralVerification}
            </span>
            <span>{runtimeMode === "static_replay" ? "STATIC FIXTURE · VERIFIER NOT EXECUTED HERE" : "LOCAL API · VERIFIER EXECUTED"}</span>
            <span>GPU RUN · {data.physicalGpuRun ? "VERIFIED" : "NONE"}</span>
            <span>PRODUCTION SIGNATURE TRUST · {data.productionSignatureTrust ? "VERIFIED" : "NONE"}</span>
          </div>

          <div className="rxp-rootline">
            <div>
              <span>FROZEN MATRIX</span>
              <strong>{data.matrixId}</strong>
            </div>
            <div>
              <span>APPEND-ONLY ROOT · {data.entryCount} ENTRIES</span>
              <code title={data.root}>{compactDigest(data.root)}</code>
            </div>
            <div className={`rxp-completeness ${data.completeness.toLowerCase()}`}>
              <span>MATRIX COVERAGE</span>
              <strong>{data.decidedCellCount}/{data.expectedCellCount} {data.completeness}</strong>
            </div>
          </div>

          <div className="rxp-chain" aria-label="RXP causal lifecycle">
            {lifecycle.map((stage, index) => (
              <div className="rxp-chain-step" key={stage}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{stage}</strong>
                <small>{index === 1 ? "one-use scope" : index === 3 ? "Merkle gate" : "digest bound"}</small>
                {index < lifecycle.length - 1 && <ArrowRight size={14} aria-hidden="true" />}
              </div>
            ))}
          </div>

          <div className="rxp-inspection">
            <div className="rxp-cell-index" role="list" aria-label="Committed matrix cells">
              {data.cells.map((cell) => (
                <button
                  type="button"
                  className={cell.cellId === selectedCell?.cellId ? "active" : ""}
                  key={cell.cellId}
                  onClick={() => setSelectedCellId(cell.cellId)}
                >
                  <span>{cell.cellId}</span>
                  <strong>{cell.state}</strong>
                  <small>{cell.evidenceCount}/7 evidence · {cell.determinismLevel.replace("_BYTE_REPLAY_VERIFIED", "")}</small>
                </button>
              ))}
            </div>
            {selectedCell && (
              <dl className="rxp-cell-detail">
                <div><dt>Intent</dt><dd><code title={selectedCell.intentDigest}>{compactDigest(selectedCell.intentDigest)}</code></dd></div>
                <div><dt>Grant</dt><dd><code title={selectedCell.grantDigest}>{compactDigest(selectedCell.grantDigest)}</code></dd></div>
                <div><dt>Receipt</dt><dd><code title={selectedCell.receiptDigest}>{compactDigest(selectedCell.receiptDigest)}</code></dd></div>
                <div><dt>Decision</dt><dd><code title={selectedCell.decisionDigest}>{compactDigest(selectedCell.decisionDigest)}</code></dd></div>
              </dl>
            )}
          </div>

          <p className="rxp-notice">
            <CircleAlert size={13} /> {data.verificationNotice}
          </p>
        </>
      ) : (
        <InlineEmpty icon={KeyRound} text="The RXP ledger endpoint did not return a verifiable protocol document." />
      )}
    </section>
  );
}

const liveAcceptanceSteps = [
  ["01", "Plan", "Matrix frozen"],
  ["02", "Review", "Independent"],
  ["03", "Approve", "R2 exact scope"],
  ["04", "Execute", "1 GPU · ≤900s"],
  ["05", "Evaluate", "Raw metrics"],
  ["06", "Verify", "Evidence Gate"],
  ["07", "Decision", "KEEP / REJECT"],
];

const databaseRoles = [
  ["runtime", "state + append", "No DELETE"],
  ["auditor", "SELECT only", "No mutation"],
  ["evidence writer", "INSERT evidence", "No memory write"],
  ["memory curator", "INSERT candidate", "No validated write"],
];

function AcceptanceReadiness({
  runtimeMode,
}: {
  runtimeMode: DashboardData["runtimeMode"];
}) {
  const [layer, setLayer] = useState<"gpu" | "database">("gpu");
  return (
    <section className="acceptance-readiness" id="acceptance" aria-labelledby="acceptance-readiness-title">
      <SectionHeading
        id="acceptance-readiness-title"
        index="JUDGE"
        title="Semifinal acceptance path"
        note="Code-ready · external execution still evidence-gated"
      />
      <div className="acceptance-truthline">
        <span><ShieldCheck size={13} /> CONTRACT PATH IMPLEMENTED</span>
        <span className="origin-warning">EXTERNAL ORIGIN · UNVERIFIED</span>
        <span>{runtimeMode === "static_replay" ? "THIS PAGE · STATIC REPLAY" : "THIS TASK · SYNTHETIC API"}</span>
      </div>
      <div className="acceptance-tabs" role="tablist" aria-label="Acceptance evidence layer">
        <button
          type="button"
          role="tab"
          aria-selected={layer === "gpu"}
          className={layer === "gpu" ? "active" : ""}
          onClick={() => setLayer("gpu")}
        >
          AgentTeams + GPU
          <small>controlled experiment chain</small>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={layer === "database"}
          className={layer === "database" ? "active" : ""}
          onClick={() => setLayer("database")}
        >
          PostgreSQL + PolarDB
          <small>durability and access boundary</small>
        </button>
      </div>
      <AnimatePresence mode="wait" initial={false}>
        {layer === "gpu" ? (
          <motion.div
            className="live-acceptance-flow"
            key="gpu-acceptance"
            role="tabpanel"
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
          >
            {liveAcceptanceSteps.map(([index, title, detail], stepIndex) => (
              <div className="live-acceptance-step" key={title}>
                <span>{index}</span>
                <strong>{title}</strong>
                <small>{detail}</small>
                {stepIndex < liveAcceptanceSteps.length - 1 && <ArrowRight size={13} aria-hidden="true" />}
              </div>
            ))}
          </motion.div>
        ) : (
          <motion.div
            className="database-boundaries"
            key="database-acceptance"
            role="tabpanel"
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
          >
            <div className="database-principle">
              <Database size={20} strokeWidth={1.4} />
              <div>
                <span>SOURCE OF TRUTH</span>
                <strong>PostgreSQL MVCC + JSONB</strong>
                <small>RLS · immutable ledgers · commit-only NOTIFY · checksum replay</small>
              </div>
            </div>
            <div className="role-matrix" aria-label="Database role write boundaries">
              {databaseRoles.map(([role, grant, denied]) => (
                <div className="role-matrix-row" key={role}>
                  <strong>{role}</strong>
                  <span>{grant}</span>
                  <small>{denied}</small>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <div className="acceptance-boundary">
        <span>LOCAL POSTGRESQL 16 · CONTRACT VERIFIED</span>
        <span>POLARDB / PITR / OFFICIAL AGENTTEAMS / GPU · NOT RUN</span>
        <a
          href="https://github.com/mythrise/ego_agent_infra/blob/main/docs/judge-feedback-implementation.md"
          target="_blank"
          rel="noreferrer"
        >
          Evidence map <ArrowRight size={12} />
        </a>
      </div>
    </section>
  );
}

function StageSpine({ current, reducedMotion }: { current: ResearchTask["stage"]; reducedMotion: boolean }) {
  const currentIndex = Math.max(0, STAGES.indexOf(current));
  return (
    <section className="stage-section" aria-labelledby="state-title">
      <SectionHeading
        id="state-title"
        index="01"
        title="Deterministic state spine"
        note={`Current gate · ${current.replaceAll("_", " ")}`}
      />
      <div className="stage-scroll" tabIndex={0} aria-label={`Workflow stage ${current} of ${STAGES.length}`}>
        <div className="stage-spine" style={{ "--stage-progress": `${(currentIndex / (STAGES.length - 1)) * 100}%` } as React.CSSProperties}>
          <div className="stage-track" aria-hidden="true">
            <div className="stage-progress" />
            {!reducedMotion && <div className="traveling-signal" />}
          </div>
          {STAGES.map((stage, index) => {
            const state = index < currentIndex ? "complete" : index === currentIndex ? "current" : "future";
            return (
              <div className={`stage-node ${state}`} key={stage} aria-current={state === "current" ? "step" : undefined}>
                <span className="stage-index">{String(index + 1).padStart(2, "0")}</span>
                <span className="stage-pip">{state === "complete" && <Check size={9} strokeWidth={2.6} />}</span>
                <span className="stage-label">{stage.replaceAll("_", " ")}</span>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function SectionHeading({ id, index, title, note }: { id?: string; index: string; title: string; note?: string }) {
  return (
    <div className="section-heading">
      <div>
        <span>{index}</span>
        <h2 id={id}>{title}</h2>
      </div>
      {note && <p>{note}</p>}
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  return <span className={`status-dot ${status}`} aria-hidden="true" />;
}

function ExperimentMatrix({ experiments }: { experiments: Experiment[] }) {
  return (
    <section className="matrix-section" id="experiments" aria-labelledby="matrix-title">
      <SectionHeading id="matrix-title" index="02" title="Experiment matrix" note={`${experiments.length} SYNTHETIC bounded arms`} />
      {experiments.length ? (
        <div className="table-scroll">
          <table className="experiment-table">
            <thead>
              <tr>
                <th>Arm / variant</th>
                <th>State</th>
                <th>Lane</th>
                <th>Manifest</th>
              </tr>
            </thead>
            <tbody>
              {experiments.map((experiment) => (
                <tr key={experiment.id}>
                  <td>
                    <span className="row-primary">{experiment.name}</span>
                    <span className="row-secondary">{experiment.variant}</span>
                  </td>
                  <td>
                    <span className="state-label"><StatusDot status={experiment.status} />{experiment.status}</span>
                  </td>
                  <td className="mono-cell">{experiment.gpuLane ?? "not run"}</td>
                  <td className="mono-cell">{experiment.manifestDigest ?? "not run"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <InlineEmpty icon={FlaskConical} text="No experiment arms have been proposed at this stage." />
      )}
    </section>
  );
}

function Sparkline({ resource }: { resource: ResourceSnapshot }) {
  const values = resource.series.length ? resource.series : [0, 0];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(1, max - min);
  const points = values
    .map((value, index) => `${(index / (values.length - 1 || 1)) * 100},${30 - ((value - min) / range) * 24}`)
    .join(" ");
  return (
    <svg className="sparkline" viewBox="0 0 100 32" preserveAspectRatio="none" role="img" aria-label={`${resource.label} trend`}>
      <path d="M0 31 H100" className="spark-baseline" />
      <polyline points={points} className="spark-path" vectorEffect="non-scaling-stroke" />
      <circle cx="100" cy={points.split(" ").at(-1)?.split(",")[1]} r="1.5" className="spark-point" />
    </svg>
  );
}

function ResourceTrace({ resources, trace }: { resources: ResourceSnapshot[]; trace: TraceEvent[] }) {
  const [visibleLogs, setVisibleLogs] = useState(1);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    setVisibleLogs(reducedMotion ? trace.length : Math.min(1, trace.length));
    if (reducedMotion || trace.length <= 1) return;
    const timer = window.setInterval(() => {
      setVisibleLogs((count) => {
        if (count >= trace.length) {
          window.clearInterval(timer);
          return count;
        }
        return count + 1;
      });
    }, 560);
    return () => window.clearInterval(timer);
  }, [trace, reducedMotion]);

  return (
    <section className="resource-section" aria-labelledby="resource-title">
      <SectionHeading id="resource-title" index="03" title="Resource trace" note="Synthetic replay · no GPU host attached" />
      {resources.length ? (
        <div className="resource-plots">
          {resources.map((resource) => (
            <div className="resource-row" key={resource.label}>
              <div className="resource-label">
                <span>{resource.label}</span>
                <strong>{formatMetric(resource.value, resource.unit)}</strong>
              </div>
              <Sparkline resource={resource} />
            </div>
          ))}
        </div>
      ) : (
        <InlineEmpty icon={Activity} text="No telemetry has been attached to this task." />
      )}
      <div className="micro-log" aria-label="Incremental control log">
        <div className="micro-log-head">
          <span>CONTROL LOG</span>
          <span>{trace.length ? `${Math.min(visibleLogs, trace.length)}/${trace.length}` : "0/0"}</span>
        </div>
        <AnimatePresence initial={false}>
          {trace.slice(0, visibleLogs).slice(-3).map((event) => (
            <motion.div
              className="micro-log-line"
              key={event.id}
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
            >
              <span>{event.at}</span>
              <strong>{event.agent}</strong>
              <p>{event.message}</p>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </section>
  );
}

function ApprovalPanel({
  gate,
  busy,
  operatorReady,
  onDecision,
}: {
  gate: ApprovalGate;
  busy: BusyAction;
  operatorReady: boolean;
  onDecision: (decision: "approve" | "reject") => void;
}) {
  return (
    <section className="approval-panel" aria-labelledby="approval-title">
      <div className="approval-sigil" aria-hidden="true"><ShieldCheck size={22} /></div>
      <div className="approval-copy">
        <div className="section-kicker">HUMAN CHECKPOINT · {gate.riskLevel}</div>
        <h2 id="approval-title">Execution is policy-blocked</h2>
        <p>{gate.summary}</p>
        <dl className="approval-facts">
          <div><dt>Requested by</dt><dd>{gate.requestedBy}</dd></div>
          <div><dt>Estimated compute</dt><dd>{gate.estimatedGpuHours ? `${gate.estimatedGpuHours} modeled GPU·h` : "not supplied"}</dd></div>
          <div><dt>Rollback point</dt><dd>{gate.rollbackPoint ?? "not supplied"}</dd></div>
          <div><dt>Expected digest</dt><dd className="mono-cell">{gate.expectedDigest || "missing — cannot approve"}</dd></div>
        </dl>
      </div>
      <div className="approval-actions">
        <button
          className="button secondary"
          onClick={() => onDecision("reject")}
          disabled={Boolean(busy) || !gate.expectedDigest || !operatorReady}
        >
          {busy === "reject" ? "Recording…" : "Reject"}
        </button>
        <button
          className="button primary"
          onClick={() => onDecision("approve")}
          disabled={Boolean(busy) || !gate.expectedDigest || !operatorReady}
        >
          {busy === "approve" ? "Recording…" : "Approve digest"}
          {busy !== "approve" && <ArrowRight size={14} />}
        </button>
      </div>
    </section>
  );
}

function EvidenceLedger({
  evidence,
  present,
  required,
  gateStatus,
  onInspect,
}: {
  evidence: EvidenceItem[];
  present: number;
  required: number;
  gateStatus: ResearchTask["gateStatus"];
  onInspect: (item: EvidenceItem) => void;
}) {
  const percent = required ? Math.min(100, (present / required) * 100) : 0;
  const verdict = gateStatus === "pass" ? "PASS" : gateStatus === "fail" ? "FAIL" : "HOLD";
  return (
    <section className="ledger-section" id="evidence" aria-labelledby="ledger-title">
      <SectionHeading id="ledger-title" index="04" title="Evidence ledger" note="SYNTHETIC artifacts before narrative claims" />
      <div className="gate-meter">
        <div>
          <span>DECISION GATE</span>
          <strong>{present} / {required} artifacts present</strong>
        </div>
        <div className="meter-track" aria-label={`${Math.round(percent)} percent evidence complete`}>
          <span style={{ width: `${percent}%` }} />
        </div>
        <span className={`gate-verdict ${gateStatus}`}>{verdict}</span>
      </div>
      {evidence.length ? (
        <div className="ledger-list">
          {evidence.map((item, index) => (
            <button className="ledger-row" onClick={() => onInspect(item)} key={item.id} aria-label={`Inspect ${item.label}`}>
              <span className="ledger-number">{String(index + 1).padStart(2, "0")}</span>
              <span className={`evidence-kind ${item.status}`}>{item.kind.replaceAll("_", " ")}</span>
              <span className="ledger-artifact">
                <strong>{item.label}</strong>
                <small>{item.source ?? "source not emitted"}</small>
              </span>
              <span className="ledger-digest">{item.digest ?? "—"}</span>
              <span className={`ledger-status ${item.status}`}><StatusDot status={item.status} />{item.status}</span>
              <ArrowRight size={13} aria-hidden="true" />
            </button>
          ))}
        </div>
      ) : (
        <InlineEmpty icon={Database} text="The evidence ledger is empty. A decision cannot be committed." />
      )}
    </section>
  );
}

function MetricComparison({ experiments }: { experiments: Experiment[] }) {
  return (
    <section className="metrics-section" aria-labelledby="metrics-title">
      <SectionHeading id="metrics-title" index="05" title="Raw metric comparison" note="SYNTHETIC fixture values · not Agent summaries" />
      {experiments.length ? (
        <div className="table-scroll metrics-scroll">
          <table className="metric-table">
            <thead>
              <tr>
                <th>Metric</th>
                {experiments.map((item) => <th key={item.id}>{item.name}</th>)}
                <th>Acceptance</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>FPS <small>higher</small></th>
                {experiments.map((item) => <td key={item.id}>{formatMetric(item.fps)}</td>)}
                <td className="target-cell">≥ 10</td>
              </tr>
              <tr>
                <th>MPJPE <small>mm · lower</small></th>
                {experiments.map((item) => <td key={item.id}>{formatMetric(item.mpjpe)}</td>)}
                <td className="target-cell">≤ +5%</td>
              </tr>
              <tr>
                <th>Latency <small>ms · lower</small></th>
                {experiments.map((item) => <td key={item.id}>{formatMetric(item.latency)}</td>)}
                <td className="target-cell">reported</td>
              </tr>
              <tr>
                <th>VRAM <small>GB · lower</small></th>
                {experiments.map((item) => <td key={item.id}>{formatMetric(item.vram)}</td>)}
                <td className="target-cell">reported</td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <InlineEmpty icon={SlidersHorizontal} text="Raw metrics will appear only after an evaluator emits them." />
      )}
    </section>
  );
}

function Inspector({
  task,
  integrations,
  selectedEvidence,
  onCloseEvidence,
}: {
  task: ResearchTask;
  integrations: IntegrationTruth[];
  selectedEvidence: EvidenceItem | null;
  onCloseEvidence: () => void;
}) {
  return (
    <aside className={`inspector ${selectedEvidence ? "detail-open" : ""}`} aria-label="Research context inspector">
      <AnimatePresence mode="wait" initial={false}>
        {selectedEvidence ? (
          <EvidenceInspector item={selectedEvidence} onClose={onCloseEvidence} />
        ) : (
          <motion.div
            key="default-inspector"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <TraceInspector trace={task.trace} />
            <MemoryPromotion task={task} />
            <IntegrationPanel integrations={integrations} />
          </motion.div>
        )}
      </AnimatePresence>
    </aside>
  );
}

function TraceInspector({ trace }: { trace: TraceEvent[] }) {
  return (
    <section className="inspector-section trace-inspector" id="trace" aria-labelledby="trace-title">
      <div className="inspector-title">
        <div><span>AUDIT EVENT REPLAY</span><h2 id="trace-title">Control-plane audit</h2></div>
        <span className="tiny-status"><span className="status-dot running" />{trace.length} events</span>
      </div>
      {trace.length ? (
        <div className="trace-list">
          {trace.slice(-6).map((event, index) => (
            <div className={`trace-event ${event.status}`} key={event.id}>
              <div className="trace-rail">
                <span>{event.kind === "mcp" ? <TerminalSquare size={12} /> : event.kind === "skill" ? <GitBranch size={12} /> : <Bot size={12} />}</span>
                {index < Math.min(trace.length, 6) - 1 && <i />}
              </div>
              <div className="trace-body">
                <div><strong>{event.agent}</strong><span>{event.at}</span></div>
                <code>{event.target}</code>
                <p>{event.message}</p>
                <small>{event.kind.toUpperCase()}{event.durationMs !== undefined ? ` · ${event.durationMs} ms` : ""}</small>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <InlineEmpty icon={Workflow} text="No audit events have been emitted for this task." />
      )}
    </section>
  );
}

function MemoryPromotion({ task }: { task: ResearchTask }) {
  const proposal = task.memoryProposal;
  return (
    <section className="inspector-section memory-section" aria-labelledby="memory-title">
      <div className="inspector-title">
          <div><span>EVIDENCE → MEMORY → SKILL</span><h2 id="memory-title">Promotion candidate</h2></div>
      </div>
      {proposal ? (
        <div className="memory-flow">
          <div className="memory-observation">
            <span>OBSERVATION</span>
            <strong>{proposal.title}</strong>
            <p>{proposal.observation}</p>
          </div>
          <div className="promotion-line"><span /><ArrowRight size={13} /></div>
          <div className="skill-candidate">
            <span>SKILL CANDIDATE</span>
            <div><strong>{proposal.candidateSkill}</strong><code>{proposal.version}</code></div>
            <p>{proposal.supportCount}/3 independently verified outcomes</p>
            <div className="promotion-meter"><span style={{ width: `${Math.min(100, (proposal.supportCount / 3) * 100)}%` }} /></div>
          </div>
          <div className="promotion-hold"><ShieldCheck size={13} /> Promotion remains human-gated</div>
        </div>
      ) : (
        <InlineEmpty icon={GitBranch} text="No procedure is eligible for skill review." />
      )}
    </section>
  );
}

function IntegrationPanel({ integrations }: { integrations: IntegrationTruth[] }) {
  return (
    <section className="inspector-section integration-section" id="integrations" aria-labelledby="integration-title">
      <div className="inspector-title">
        <div><span>CONNECTION CLAIMS</span><h2 id="integration-title">Integration truth</h2></div>
      </div>
      <p className="truth-note">Only “connected” rows represent a verified endpoint in this running stack.</p>
      {integrations.length ? (
        <div className="integration-list">
          {integrations.map((item) => (
            <details className="integration-row" key={item.id}>
              <summary>
                <StatusDot status={item.status} />
                <span><strong>{item.name}</strong><small>{item.mode}</small></span>
                <ChevronDown size={13} />
              </summary>
              <p>{item.detail}</p>
              <span className={`truth-state ${item.status}`}>{item.status}</span>
            </details>
          ))}
        </div>
      ) : (
        <InlineEmpty icon={Network} text="The integration endpoint returned no connection claims." />
      )}
    </section>
  );
}

function EvidenceInspector({ item, onClose }: { item: EvidenceItem; onClose: () => void }) {
  return (
    <motion.section
      key={item.id}
      className="evidence-inspector"
      initial={{ opacity: 0, x: 16 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 12 }}
      aria-labelledby="evidence-inspector-title"
    >
      <button className="inspector-close" onClick={onClose} aria-label="Close evidence inspector"><X size={16} /></button>
      <span className="evidence-kind detail-kind">{item.kind.replaceAll("_", " ")}</span>
      <h2 id="evidence-inspector-title">{item.label}</h2>
      <p className="detail-intro">Evidence metadata from the selected runtime. Static replay records are illustrative SYNTHETIC fixtures; no generated summary substitutes for an underlying artifact.</p>
      <dl className="detail-list">
        <div><dt>Status</dt><dd><span className={`ledger-status ${item.status}`}><StatusDot status={item.status} />{item.status}</span></dd></div>
        <div><dt>Digest</dt><dd><code>{item.digest ?? "not emitted"}</code></dd></div>
        <div><dt>Source</dt><dd>{item.source ?? "not emitted"}</dd></div>
        <div><dt>Verified by</dt><dd>{item.verifiedBy ?? "not independently verified"}</dd></div>
        <div><dt>Recorded at</dt><dd>{item.createdAt ?? "not emitted"}</dd></div>
        <div><dt>Evidence ID</dt><dd><code>{item.id}</code></dd></div>
      </dl>
      {item.raw && (
        <details className="raw-payload">
          <summary>Raw evidence payload <ChevronDown size={13} /></summary>
          <pre>{JSON.stringify(item.raw, null, 2)}</pre>
        </details>
      )}
      <div className="inspector-rule">
        <ShieldCheck size={15} />
        <p><strong>Decision invariant</strong> Missing or unverified required evidence keeps the decision gate closed.</p>
      </div>
    </motion.section>
  );
}

function ActionDock({
  task,
  busy,
  approvalToken,
  onReset,
  onAdvance,
  onAutorun,
  runtimeMode,
  operatorConnected,
}: {
  task: ResearchTask;
  busy: BusyAction;
  approvalToken?: string;
  onReset: () => void;
  onAdvance: () => void;
  onAutorun: () => void;
  runtimeMode: DashboardData["runtimeMode"];
  operatorConnected: boolean;
}) {
  const approvalBlocked = task.stage === "APPROVAL" && !approvalToken;
  const operatorLocked = runtimeMode === "local_api" && !operatorConnected;
  const terminal = task.stage === "COMPLETED";
  return (
    <div className="action-dock" aria-label="Task controls">
      <div className="dock-state">
        <span className="status-dot running" />
        <div><small>{runtimeMode === "static_replay" ? "BROWSER REPLAY" : "CONTROL PLANE"}</small><strong>{task.stage.replaceAll("_", " ")}</strong></div>
      </div>
      <div className="dock-actions">
        <button className="button ghost" onClick={onReset} disabled={Boolean(busy) || operatorLocked}>
          <RotateCcw size={13} />{busy === "reset" ? "Resetting…" : "Reset demo"}
        </button>
        <button className="button secondary" onClick={onAdvance} disabled={Boolean(busy) || operatorLocked || approvalBlocked || terminal}>
          {busy === "advance" ? "Advancing…" : "Advance once"}
        </button>
        <button className="button primary" onClick={onAutorun} disabled={Boolean(busy) || operatorLocked || approvalBlocked || terminal}>
          <Play size={13} fill="currentColor" />{busy === "autorun" ? "Running…" : "Run to next gate"}
        </button>
      </div>
      {(operatorLocked || approvalBlocked) && (
        <span className="dock-hint">
          {operatorLocked
            ? "Connect operator session to mutate"
            : runtimeMode === "static_replay" ? "Synthetic browser grant required" : "Approval token required"}
        </span>
      )}
    </div>
  );
}

function InlineEmpty({ icon: Icon, text }: { icon: typeof Activity; text: string }) {
  return (
    <div className="inline-empty">
      <Icon size={18} strokeWidth={1.4} aria-hidden="true" />
      <p>{text}</p>
    </div>
  );
}

function LoadingScreen() {
  return (
    <div className="state-screen loading-screen" role="status">
      <NoiseField />
      <div className="loading-brand"><span className="brand-glyph">E</span><strong>EgoAgentOS</strong></div>
      <div className="loader-line"><span /></div>
      <p>Preparing the ResearchOps runtime…</p>
      <div className="skeleton-lines" aria-hidden="true"><i /><i /><i /></div>
    </div>
  );
}

function ErrorScreen({ message, onRetry, onFixture }: { message: string; onRetry: () => void; onFixture: () => void }) {
  return (
    <div className="state-screen error-screen">
      <NoiseField />
      <div className="state-icon"><CircleAlert size={23} /></div>
      <span className="eyebrow">CONTROL PLANE UNAVAILABLE</span>
      <h1>The cockpit could not establish a verified data path.</h1>
      <p>{message}</p>
      <div className="state-actions">
        <button className="button primary" onClick={onRetry}><RefreshCcw size={14} />Retry connection</button>
        <button className="button secondary" onClick={onFixture}>Open labeled fixture</button>
      </div>
      <small>The local fixture is synthetic and disables automatic refresh.</small>
    </div>
  );
}

function EmptyScreen({ onReset }: { onReset: () => void }) {
  return (
    <div className="state-screen empty-screen">
      <NoiseField />
      <div className="state-icon"><Database size={22} /></div>
      <span className="eyebrow">NO ACTIVE RESEARCH TASK</span>
      <h1>The control plane is ready, but the task ledger is empty.</h1>
      <p>Restore the bounded synthetic scenario to inspect the complete evidence-gated workflow.</p>
      <button className="button primary" onClick={onReset}><RotateCcw size={14} />Reset synthetic demo</button>
    </div>
  );
}

export default App;
export { approvalTokenForGeneration, EvidenceLedger, RXPProtocolView, StageSpine };
