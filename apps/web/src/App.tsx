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
import { useI18n } from "./i18n";
import type { TranslationKey } from "./i18n";
import { LandingPage } from "./LandingPage";
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

const navItems: Array<{ id: string; labelKey: TranslationKey; icon: typeof Bot }> = [
  { id: "compose", labelKey: "nav.compose", icon: Bot },
  { id: "cockpit", labelKey: "nav.cockpit", icon: Gauge },
  { id: "acceptance", labelKey: "nav.acceptance", icon: ShieldCheck },
  { id: "experiments", labelKey: "nav.experiments", icon: FlaskConical },
  { id: "protocol", labelKey: "nav.protocol", icon: KeyRound },
  { id: "evidence", labelKey: "nav.evidence", icon: FileCheck2 },
  { id: "trace", labelKey: "nav.trace", icon: Workflow },
  { id: "integrations", labelKey: "nav.integrations", icon: Network },
];

const sourceLinks = [
  {
    labelKey: "nav.github" as TranslationKey,
    href: "https://github.com/mythrise/ego_agent_infra",
    icon: Code2,
  },
  {
    labelKey: "nav.readme" as TranslationKey,
    href: "https://github.com/mythrise/ego_agent_infra#readme",
    icon: BookOpen,
  },
  {
    labelKey: "nav.notices" as TranslationKey,
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
    <div className="experience-root">
      <LandingPage />
      <div className="app-shell research-cockpit">
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
            <ResearchComposer runtimeMode={dashboard.runtimeMode} />
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
        </main>

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
  const { language, t } = useI18n();
  const [key, setKey] = useState("");

  return (
    <section className="operator-strip" aria-label={language === "zh" ? "操作者会话" : "Operator session"}>
      <div className={`operator-state ${connected ? "connected" : "locked"}`}>
        <KeyRound size={13} aria-hidden="true" />
        <span>{connected ? (language === "zh" ? "操作者已连接" : "OPERATOR CONNECTED") : (language === "zh" ? "写操作已锁定" : "MUTATIONS LOCKED")}</span>
        <small>{connected ? (language === "zh" ? "仅存于内存 · 刷新标签页后清除" : "MEMORY ONLY · CLEAR ON TAB RELOAD") : (language === "zh" ? "需要访问密钥" : "BEARER KEY REQUIRED")}</small>
      </div>
      {connected ? (
        <button className="operator-clear" type="button" onClick={onClear}>
          {t("action.clearSession")}
        </button>
      ) : (
        <form
          className="operator-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (onConnect(key)) setKey("");
          }}
        >
          <label htmlFor="operator-session-key">{language === "zh" ? "会话操作者密钥" : "Session operator key"}</label>
          <input
            id="operator-session-key"
            aria-label={language === "zh" ? "操作者会话密钥" : "Operator session key"}
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={key}
            onChange={(event) => setKey(event.target.value)}
            placeholder={language === "zh" ? "粘贴部署密钥" : "Paste deployment key"}
          />
          <button type="submit" disabled={!key}>{t("action.connect")}</button>
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
  const { language, setLanguage, t } = useI18n();
  return (
    <>
      {open && <button className="nav-scrim" aria-label={t("nav.close")} onClick={onClose} />}
      <aside className={`rail ${open ? "is-open" : ""}`} aria-label={t("nav.primary")}>
        <a className="brand-mark" href="#compose" aria-label="EgoAgentOS ResearchOps home" onClick={onClose}>
          <span className="brand-glyph">E</span>
          <span className="brand-type">EgoAgentOS</span>
        </a>
        <nav className="rail-nav">
          {navItems.map(({ id, labelKey, icon: Icon }, index) => (
            <a
              href={`#${id}`}
              className={index === 0 ? "active" : ""}
              key={id}
              title={t(labelKey)}
              aria-current={index === 0 ? "page" : undefined}
              onClick={onClose}
            >
              <Icon size={17} strokeWidth={1.6} aria-hidden="true" />
              <span>{t(labelKey)}</span>
            </a>
          ))}
          <span className="rail-divider" aria-hidden="true" />
          {sourceLinks.map(({ labelKey, href, icon: Icon }) => (
            <a href={href} key={labelKey} title={t(labelKey)} target="_blank" rel="noreferrer" onClick={onClose}>
              <Icon size={17} strokeWidth={1.6} aria-hidden="true" />
              <span>{t(labelKey)}</span>
            </a>
          ))}
        </nav>
        <div className="rail-language" role="group" aria-label={t("language.switch")}>
          <button className={language === "en" ? "active" : ""} onClick={() => setLanguage("en")} aria-pressed={language === "en"}>EN</button>
          <button className={language === "zh" ? "active" : ""} onClick={() => setLanguage("zh")} aria-pressed={language === "zh"}>中</button>
        </div>
        <div className="rail-footer">
          <span className="mode-light" aria-hidden="true" />
          <span>{runtimeMode === "static_replay" ? <>{t("runtime.browserReplay")}<br />{t("runtime.noLiveServices")}</> : <>{t("runtime.syntheticApi")}</>}</span>
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
  const { t } = useI18n();
  const staticReplay = runtimeMode === "static_replay";
  return (
    <header className="topbar">
      <button className="icon-button mobile-menu" onClick={onMenu} aria-label={t("nav.open")}>
        <Menu size={18} />
      </button>
      <div className="context-line">
        <span className="eyebrow">RESEARCHOPS / TASK</span>
        <label className="task-picker">
          <span className="sr-only">{t("task.selected")}</span>
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
          <span className="demo-label-full">{staticReplay ? t("runtime.staticBadge") : t("runtime.localBadge")}</span>
          <span className="demo-label-mobile">{staticReplay ? t("runtime.staticBadgeShort") : t("runtime.localBadgeShort")}</span>
        </span>
        <span className="sync-time">{t("runtime.updated", { time: formatTime(task.updatedAt) })}</span>
        {!staticReplay && (
          <button
            className={`icon-button ${autoRefresh ? "is-active" : ""}`}
            onClick={onToggleAuto}
            aria-label={autoRefresh ? t("action.pauseRefresh") : t("action.resumeRefresh")}
            title={autoRefresh ? "Auto refresh on" : "Auto refresh paused"}
          >
            {autoRefresh ? <Pause size={14} /> : <Play size={14} />}
          </button>
        )}
        <button className="icon-button" onClick={onRefresh} aria-label={t("action.refresh")}>
          <RefreshCcw className={refreshing ? "spin" : ""} size={14} />
        </button>
      </div>
    </header>
  );
}

function TaskCommand({ task, runtimeMode }: { task: ResearchTask; runtimeMode: DashboardData["runtimeMode"] }) {
  const { t } = useI18n();
  const staticReplay = runtimeMode === "static_replay";
  return (
    <section className="task-command" id="cockpit" aria-labelledby="task-title">
      <div className="command-main">
        <div className="section-kicker">
          <span className={`live-mark ${task.stage === "COMPLETED" || staticReplay ? "settled" : ""}`} />
          {staticReplay
            ? task.stage === "COMPLETED" ? t("task.syntheticCompleted") : t("task.syntheticCheckpoint")
            : task.stage === "COMPLETED" ? t("task.completed") : t("task.activeGoal")}
        </div>
        <h2 id="task-title">{task.title}</h2>
        <p>{task.objective}</p>
      </div>
      <div className="acceptance-strip" aria-label={t("task.acceptance")}>
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
            <span>{t("task.acceptanceSchema")}</span>
            <strong>{t("task.notSupplied")}</strong>
          </div>
        )}
        <div className="acceptance-item risk-item">
          <span>{t("task.policyClass")}</span>
          <strong>{task.riskLevel} <small>{t("task.approvalBounded")}</small></strong>
        </div>
        {task.decision && (
          <div className="acceptance-item decision-item">
            <span>{t("task.verifiedDecision")}</span>
            <strong>{task.decision} <small>{t("task.gateBound")}</small></strong>
          </div>
        )}
      </div>
    </section>
  );
}

type ComposerLevel = "detailed" | "idea" | "baseline";

const composerLevels: Array<{ id: ComposerLevel; labelKey: TranslationKey; detailKey: TranslationKey }> = [
  { id: "detailed", labelKey: "composer.level.detailed", detailKey: "composer.level.detailedDetail" },
  { id: "idea", labelKey: "composer.level.idea", detailKey: "composer.level.ideaDetail" },
  { id: "baseline", labelKey: "composer.level.baseline", detailKey: "composer.level.baselineDetail" },
];

const composerModeDefinitions: Record<ComposerLevel, {
  guideKey: TranslationKey;
  placeholderKey: TranslationKey;
  exampleKey: TranslationKey;
  requirementKeys: TranslationKey[];
}> = {
  detailed: {
    guideKey: "composer.mode.detailedGuide",
    placeholderKey: "composer.prompt.detailed",
    exampleKey: "composer.example.detailed",
    requirementKeys: [
      "composer.requirement.detailedBaseline",
      "composer.requirement.detailedBranches",
      "composer.requirement.detailedEvidence",
    ],
  },
  idea: {
    guideKey: "composer.mode.ideaGuide",
    placeholderKey: "composer.prompt.idea",
    exampleKey: "composer.example.idea",
    requirementKeys: [
      "composer.requirement.ideaBaseline",
      "composer.requirement.ideaDirection",
      "composer.requirement.ideaConstraints",
    ],
  },
  baseline: {
    guideKey: "composer.mode.baselineGuide",
    placeholderKey: "composer.prompt.baseline",
    exampleKey: "composer.example.baseline",
    requirementKeys: [
      "composer.requirement.baselineCode",
      "composer.requirement.baselineMetrics",
      "composer.requirement.baselineBudget",
    ],
  },
};

export function ResearchComposer({ runtimeMode }: { runtimeMode: DashboardData["runtimeMode"] }) {
  const { t } = useI18n();
  const [level, setLevel] = useState<ComposerLevel>("detailed");
  const [inputs, setInputs] = useState<Record<ComposerLevel, string>>({
    detailed: "",
    idea: "",
    baseline: "",
  });
  const [compiled, setCompiled] = useState(false);
  const mode = composerModeDefinitions[level];
  const prompt = inputs[level];

  const updatePrompt = (value: string) => {
    setInputs((current) => ({ ...current, [level]: value }));
    setCompiled(false);
  };

  const loadExample = () => {
    updatePrompt(t(mode.exampleKey));
  };

  return (
    <section className="research-composer" id="compose" aria-labelledby="composer-title">
      <div className="composer-hero">
        <div className="composer-copy">
          <div className="composer-badge"><span /> {t("composer.badge")}</div>
          <h1 id="composer-title">{t("composer.titleLine1")}<br />{t("composer.titleLine2")}</h1>
          <p>{t("composer.lede")}</p>
        </div>
        <div className="composer-proof" aria-label="System properties">
          <div><strong>13</strong><span>{t("composer.proof.stages")}</span></div>
          <div><strong>RXP</strong><span>{t("composer.proof.identity")}</span></div>
          <div><strong>L0–L3</strong><span>{t("composer.proof.memory")}</span></div>
        </div>
      </div>

      <div className="composer-surface">
        <div className="composer-levels" role="tablist" aria-label={t("composer.inputLevel")}>
          {composerLevels.map((item, index) => (
            <button
              type="button"
              role="tab"
              aria-selected={level === item.id}
              className={level === item.id ? "active" : ""}
              onClick={() => { setLevel(item.id); setCompiled(false); }}
              key={item.id}
            >
              <small>0{index + 1}</small>
              <span><strong>{t(item.labelKey)}</strong><em>{t(item.detailKey)}</em></span>
            </button>
          ))}
        </div>
        <div className="composer-workbench" aria-label={t("composer.workspaceLabel")}>
          <div className="composer-input">
            <div className="composer-pane-heading">
              <div>
                <span>{t("composer.customInput")}</span>
                <h3>{t(composerLevels.find((item) => item.id === level)?.labelKey ?? "composer.level.detailed")}</h3>
                <p id={`composer-guide-${level}`}>{t(mode.guideKey)}</p>
              </div>
              <button type="button" className="composer-clear" onClick={() => updatePrompt("")} disabled={!prompt}>
                {t("composer.clear")}
              </button>
            </div>
            <label htmlFor={`research-prompt-${level}`}>{t("composer.customInputHint")}</label>
            <textarea
              id={`research-prompt-${level}`}
              aria-describedby={`composer-guide-${level}`}
              value={prompt}
              onChange={(event) => updatePrompt(event.target.value)}
              rows={12}
              placeholder={t(mode.placeholderKey)}
            />
            <div className="composer-requirements" aria-label={t("composer.requirements")}>
              <strong>{t("composer.requirements")}</strong>
              <ul>
                {mode.requirementKeys.map((key) => (
                  <li key={key}><Check size={13} aria-hidden="true" />{t(key)}</li>
                ))}
              </ul>
            </div>
            <div className="composer-input-foot">
              <span>
                {runtimeMode === "static_replay"
                  ? t("composer.browserExplainer")
                  : t("composer.localControlPlane")}
                <em>{t("composer.characters", { count: prompt.length })}</em>
              </span>
              <button type="button" onClick={() => setCompiled(true)} disabled={!prompt.trim()}>
                {t("composer.compile")} <ArrowRight size={15} />
              </button>
            </div>
          </div>
          <aside className="composer-example" aria-labelledby={`composer-example-${level}`}>
            <div className="composer-example-heading">
              <div>
                <span>{t("composer.exampleNote")}</span>
                <h3 id={`composer-example-${level}`}>{t("composer.exampleTitle")}</h3>
              </div>
              <span className="composer-example-mode">0{composerLevels.findIndex((item) => item.id === level) + 1}</span>
            </div>
            <div className="composer-example-content">{t(mode.exampleKey)}</div>
            <button type="button" className="composer-example-action" onClick={loadExample}>
              <Check size={15} aria-hidden="true" /> {t("composer.loadExample")}
            </button>
          </aside>
        </div>
      </div>

      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          className={`composer-chain ${compiled ? "is-compiled" : ""}`}
          key={`${level}-${compiled}`}
          initial={{ opacity: 0.75, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <div className="chain-node source">
            <span>{t("composer.chain.input")}</span><strong>{t(composerLevels.find((item) => item.id === level)?.labelKey ?? "composer.level.detailed")}</strong>
            <small>{compiled ? t("composer.chain.normalized") : t("composer.chain.waiting")}</small>
          </div>
          <ArrowRight className="chain-arrow" size={16} />
          <div className="chain-node tree">
            <span>{t("composer.chain.tree")}</span><strong>{t("composer.chain.treeValue")}</strong>
            <div className="mini-tree" aria-hidden="true"><i /><i /><i /><i /></div>
          </div>
          <ArrowRight className="chain-arrow" size={16} />
          <div className="chain-node matrix">
            <span>{t("composer.chain.matrix")}</span><strong>{compiled ? t("composer.chain.cells", { count: 165 }) : t("composer.chain.matrixFormula")}</strong>
            <small>{t("composer.chain.intent")}</small>
          </div>
          <ArrowRight className="chain-arrow" size={16} />
          <div className="chain-node evidence">
            <span>{t("composer.chain.gate")}</span><strong>{t("composer.chain.evidence")}</strong>
            <small>{t("composer.chain.veto")}</small>
          </div>
          <ArrowRight className="chain-arrow" size={16} />
          <div className="chain-node memory">
            <span>{t("composer.chain.compact")}</span><strong>{t("composer.chain.focus")}</strong>
            <small>{compiled ? t("composer.chain.context", { before: "18.4k", after: "1.7k" }) : t("composer.chain.freshness")}</small>
          </div>
        </motion.div>
      </AnimatePresence>
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
  const { status, t } = useI18n();
  const [selectedCellId, setSelectedCellId] = useState("");
  const selectedCell = data?.cells.find((cell) => cell.cellId === selectedCellId) ?? data?.cells[0];
  const lifecycle = ["protocol.intent", "protocol.grant", "protocol.receipt", "protocol.evidence", "protocol.decision"] as const;

  return (
    <section className="rxp-section" id="protocol" aria-labelledby="rxp-title">
      <SectionHeading
        id="rxp-title"
        index="RXP/1"
        title={t("protocol.title")}
        note={t("protocol.note")}
      />
      {data ? (
        <>
          <div className="rxp-truthline">
            <span className={`rxp-verdict ${data.structuralVerification.toLowerCase()}`}>
              <ShieldCheck size={14} /> {t("protocol.structure", { status: status(data.structuralVerification) })}
            </span>
            <span>{runtimeMode === "static_replay" ? t("protocol.staticVerifier") : t("protocol.localVerifier")}</span>
            <span>{t("protocol.gpuRun", { status: data.physicalGpuRun ? t("status.verified") : t("status.none") })}</span>
            <span>{t("protocol.signatureTrust", { status: data.productionSignatureTrust ? t("status.verified") : t("status.none") })}</span>
          </div>

          <div className="rxp-rootline">
            <div>
              <span>{t("protocol.frozenMatrix")}</span>
              <strong>{data.matrixId}</strong>
            </div>
            <div>
              <span>{t("protocol.appendRoot", { count: data.entryCount })}</span>
              <code title={data.root}>{compactDigest(data.root)}</code>
            </div>
            <div className={`rxp-completeness ${data.completeness.toLowerCase()}`}>
              <span>{t("protocol.matrixCoverage")}</span>
              <strong>{data.decidedCellCount}/{data.expectedCellCount} {status(data.completeness)}</strong>
            </div>
          </div>

          <div className="rxp-chain" aria-label={t("protocol.lifecycle")}>
            {lifecycle.map((stageKey, index) => (
              <div className="rxp-chain-step" key={stageKey}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{t(stageKey)}</strong>
                <small>{index === 1 ? t("protocol.oneUseScope") : index === 3 ? t("protocol.merkleGate") : t("protocol.digestBound")}</small>
                {index < lifecycle.length - 1 && <ArrowRight size={14} aria-hidden="true" />}
              </div>
            ))}
          </div>

          <div className="rxp-inspection">
            <div className="rxp-cell-index" role="list" aria-label={t("protocol.committedCells")}>
              {data.cells.map((cell) => (
                <button
                  type="button"
                  className={cell.cellId === selectedCell?.cellId ? "active" : ""}
                  key={cell.cellId}
                  onClick={() => setSelectedCellId(cell.cellId)}
                >
                  <span>{cell.cellId}</span>
                  <strong>{status(cell.state)}</strong>
                  <small>{t("protocol.evidenceCount", { count: cell.evidenceCount })} · {cell.determinismLevel.replace("_BYTE_REPLAY_VERIFIED", "")}</small>
                </button>
              ))}
            </div>
            {selectedCell && (
              <dl className="rxp-cell-detail">
                <div><dt>{t("protocol.intent")}</dt><dd><code title={selectedCell.intentDigest}>{compactDigest(selectedCell.intentDigest)}</code></dd></div>
                <div><dt>{t("protocol.grant")}</dt><dd><code title={selectedCell.grantDigest}>{compactDigest(selectedCell.grantDigest)}</code></dd></div>
                <div><dt>{t("protocol.receipt")}</dt><dd><code title={selectedCell.receiptDigest}>{compactDigest(selectedCell.receiptDigest)}</code></dd></div>
                <div><dt>{t("protocol.decision")}</dt><dd><code title={selectedCell.decisionDigest}>{compactDigest(selectedCell.decisionDigest)}</code></dd></div>
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
  const { language, t } = useI18n();
  const [layer, setLayer] = useState<"gpu" | "database">("gpu");
  const localizedSteps = language === "zh" ? [
    ["01", "规划", "矩阵已冻结"],
    ["02", "审查", "独立审查"],
    ["03", "批准", "R2 精确权限"],
    ["04", "执行", "1 GPU · ≤900 秒"],
    ["05", "评测", "原始指标"],
    ["06", "验证", "证据门禁"],
    ["07", "决策", "保留 / 拒绝"],
  ] : liveAcceptanceSteps;
  const localizedRoles = language === "zh" ? [
    ["运行时", "状态 + 只追加", "禁止删除"],
    ["审计员", "仅查询", "禁止写入"],
    ["证据写入者", "写入证据", "禁止写记忆"],
    ["记忆策展者", "写入候选", "禁止写入已验证区"],
  ] : databaseRoles;
  return (
    <section className="acceptance-readiness" id="acceptance" aria-labelledby="acceptance-readiness-title">
      <SectionHeading
        id="acceptance-readiness-title"
        index="JUDGE"
        title={t("acceptance.title")}
        note={t("acceptance.note")}
      />
      <div className="acceptance-truthline">
        <span><ShieldCheck size={13} /> {t("acceptance.contract")}</span>
        <span className="origin-warning">{t("acceptance.externalOrigin")}</span>
        <span>{runtimeMode === "static_replay" ? t("acceptance.staticPage") : t("acceptance.syntheticTask")}</span>
      </div>
      <div className="acceptance-tabs" role="tablist" aria-label={t("acceptance.layer")}>
        <button
          type="button"
          role="tab"
          aria-selected={layer === "gpu"}
          className={layer === "gpu" ? "active" : ""}
          onClick={() => setLayer("gpu")}
        >
          {t("acceptance.agentTeams")}
          <small>{t("acceptance.agentTeamsDetail")}</small>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={layer === "database"}
          className={layer === "database" ? "active" : ""}
          onClick={() => setLayer("database")}
        >
          {t("acceptance.database")}
          <small>{t("acceptance.databaseDetail")}</small>
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
            {localizedSteps.map(([index, title, detail], stepIndex) => (
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
                <span>{language === "zh" ? "事实源" : "SOURCE OF TRUTH"}</span>
                <strong>{language === "zh" ? "TDSQL Nexa · SQL 权限边界" : "TDSQL Nexa · SQL authority"}</strong>
                <small>{language === "zh" ? "MVCC · 不可变账本 · 每 Agent L0–L3 · 校验和重放" : "MVCC · immutable ledgers · per-agent L0–L3 · checksum replay"}</small>
              </div>
            </div>
            <div className="role-matrix" aria-label={language === "zh" ? "数据库角色写入边界" : "Database role write boundaries"}>
              {localizedRoles.map(([role, grant, denied]) => (
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
        <span>{language === "zh" ? "本地每 Agent 数据库 + FOCUS.MD · 合同已验证" : "LOCAL PER-AGENT DB + FOCUS.MD · CONTRACT VERIFIED"}</span>
        <span>{language === "zh" ? "TDSQL NEXA / AGENT MEMORY / 官方 AGENTTEAMS / GPU · 未运行" : "TDSQL NEXA / AGENT MEMORY / OFFICIAL AGENTTEAMS / GPU · NOT RUN"}</span>
        <a
          href="https://github.com/mythrise/ego_agent_infra/blob/main/docs/judge-feedback-implementation.md"
          target="_blank"
          rel="noreferrer"
        >
          {language === "zh" ? "证据地图" : "Evidence map"} <ArrowRight size={12} />
        </a>
      </div>
    </section>
  );
}

function StageSpine({ current, reducedMotion }: { current: ResearchTask["stage"]; reducedMotion: boolean }) {
  const { language, stage: stageLabel } = useI18n();
  const currentIndex = Math.max(0, STAGES.indexOf(current));
  return (
    <section className="stage-section" aria-labelledby="state-title">
      <SectionHeading
        id="state-title"
        index="01"
        title={language === "zh" ? "确定性状态脊柱" : "Deterministic state spine"}
        note={`${language === "zh" ? "当前门禁" : "Current gate"} · ${stageLabel(current)}`}
      />
      <div className="stage-scroll" tabIndex={0} aria-label={`${language === "zh" ? "工作流阶段" : "Workflow stage"} ${stageLabel(current)} / ${STAGES.length}`}>
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
                <span className="stage-label">{language === "zh" ? stageLabel(stage) : stage.replaceAll("_", " ")}</span>
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
  const { language, status } = useI18n();
  return (
    <section className="matrix-section" id="experiments" aria-labelledby="matrix-title">
      <SectionHeading id="matrix-title" index="02" title={language === "zh" ? "实验矩阵" : "Experiment matrix"} note={language === "zh" ? `${experiments.length} 条合成数据受控支线` : `${experiments.length} SYNTHETIC bounded arms`} />
      {experiments.length ? (
        <div className="table-scroll">
          <table className="experiment-table">
            <thead>
              <tr>
                <th>{language === "zh" ? "支线 / 变体" : "Arm / variant"}</th>
                <th>{language === "zh" ? "状态" : "State"}</th>
                <th>{language === "zh" ? "计算通道" : "Lane"}</th>
                <th>{language === "zh" ? "清单摘要" : "Manifest"}</th>
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
                    <span className="state-label"><StatusDot status={experiment.status} />{status(experiment.status)}</span>
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
  const { language } = useI18n();
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
      <SectionHeading id="resource-title" index="03" title={language === "zh" ? "资源轨迹" : "Resource trace"} note={language === "zh" ? "合成回放 · 未连接 GPU 主机" : "Synthetic replay · no GPU host attached"} />
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
          <span>{language === "zh" ? "控制日志" : "CONTROL LOG"}</span>
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
  const { language, t } = useI18n();
  return (
    <section className="approval-panel" aria-labelledby="approval-title">
      <div className="approval-sigil" aria-hidden="true"><ShieldCheck size={22} /></div>
      <div className="approval-copy">
        <div className="section-kicker">{language === "zh" ? "人工检查点" : "HUMAN CHECKPOINT"} · {gate.riskLevel}</div>
        <h2 id="approval-title">{language === "zh" ? "执行已被策略门禁阻断" : "Execution is policy-blocked"}</h2>
        <p>{gate.summary}</p>
        <dl className="approval-facts">
          <div><dt>{language === "zh" ? "请求者" : "Requested by"}</dt><dd>{gate.requestedBy}</dd></div>
          <div><dt>{language === "zh" ? "预计算力" : "Estimated compute"}</dt><dd>{gate.estimatedGpuHours ? `${gate.estimatedGpuHours} modeled GPU·h` : t("task.notSupplied")}</dd></div>
          <div><dt>{language === "zh" ? "回滚点" : "Rollback point"}</dt><dd>{gate.rollbackPoint ?? t("task.notSupplied")}</dd></div>
          <div><dt>{language === "zh" ? "期望摘要" : "Expected digest"}</dt><dd className="mono-cell">{gate.expectedDigest || (language === "zh" ? "缺失，无法批准" : "missing — cannot approve")}</dd></div>
        </dl>
      </div>
      <div className="approval-actions">
        <button
          className="button secondary"
          onClick={() => onDecision("reject")}
          disabled={Boolean(busy) || !gate.expectedDigest || !operatorReady}
        >
          {busy === "reject" ? t("action.recording") : t("action.reject")}
        </button>
        <button
          className="button primary"
          onClick={() => onDecision("approve")}
          disabled={Boolean(busy) || !gate.expectedDigest || !operatorReady}
        >
          {busy === "approve" ? t("action.recording") : t("action.approve")}
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
  const { status, t } = useI18n();
  const percent = required ? Math.min(100, (present / required) * 100) : 0;
  const verdict = gateStatus === "pass" ? t("status.pass") : gateStatus === "fail" ? t("status.fail") : t("status.hold");
  return (
    <section className="ledger-section" id="evidence" aria-labelledby="ledger-title">
      <SectionHeading id="ledger-title" index="04" title={t("evidence.title")} note={t("evidence.note")} />
      <div className="gate-meter">
        <div>
          <span>{t("evidence.decisionGate")}</span>
          <strong>{t("evidence.artifactsPresent", { present, required })}</strong>
        </div>
        <div className="meter-track" aria-label={t("evidence.progress", { percent: Math.round(percent) })}>
          <span style={{ width: `${percent}%` }} />
        </div>
        <span className={`gate-verdict ${gateStatus}`}>{verdict}</span>
      </div>
      {evidence.length ? (
        <div className="ledger-list">
          {evidence.map((item, index) => (
            <button className="ledger-row" onClick={() => onInspect(item)} key={item.id} aria-label={t("evidence.inspect", { label: item.label })}>
              <span className="ledger-number">{String(index + 1).padStart(2, "0")}</span>
              <span className={`evidence-kind ${item.status}`}>{item.kind.replaceAll("_", " ")}</span>
              <span className="ledger-artifact">
                <strong>{item.label}</strong>
                <small>{item.source ?? t("evidence.sourceMissing")}</small>
              </span>
              <span className="ledger-digest">{item.digest ?? "—"}</span>
              <span className={`ledger-status ${item.status}`}><StatusDot status={item.status} />{status(item.status)}</span>
              <ArrowRight size={13} aria-hidden="true" />
            </button>
          ))}
        </div>
      ) : (
        <InlineEmpty icon={Database} text={t("evidence.empty")} />
      )}
    </section>
  );
}

function MetricComparison({ experiments }: { experiments: Experiment[] }) {
  const { language } = useI18n();
  return (
    <section className="metrics-section" aria-labelledby="metrics-title">
      <SectionHeading id="metrics-title" index="05" title={language === "zh" ? "原始指标对比" : "Raw metric comparison"} note={language === "zh" ? "合成样例数值 · 非 Agent 总结" : "SYNTHETIC fixture values · not Agent summaries"} />
      {experiments.length ? (
        <div className="table-scroll metrics-scroll">
          <table className="metric-table">
            <thead>
              <tr>
                <th>{language === "zh" ? "指标" : "Metric"}</th>
                {experiments.map((item) => <th key={item.id}>{item.name}</th>)}
                <th>{language === "zh" ? "验收条件" : "Acceptance"}</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>FPS <small>{language === "zh" ? "越高越好" : "higher"}</small></th>
                {experiments.map((item) => <td key={item.id}>{formatMetric(item.fps)}</td>)}
                <td className="target-cell">≥ 10</td>
              </tr>
              <tr>
                <th>MPJPE <small>{language === "zh" ? "mm · 越低越好" : "mm · lower"}</small></th>
                {experiments.map((item) => <td key={item.id}>{formatMetric(item.mpjpe)}</td>)}
                <td className="target-cell">≤ +5%</td>
              </tr>
              <tr>
                <th>{language === "zh" ? "延迟" : "Latency"} <small>{language === "zh" ? "ms · 越低越好" : "ms · lower"}</small></th>
                {experiments.map((item) => <td key={item.id}>{formatMetric(item.latency)}</td>)}
                <td className="target-cell">{language === "zh" ? "需报告" : "reported"}</td>
              </tr>
              <tr>
                <th>VRAM <small>{language === "zh" ? "GB · 越低越好" : "GB · lower"}</small></th>
                {experiments.map((item) => <td key={item.id}>{formatMetric(item.vram)}</td>)}
                <td className="target-cell">{language === "zh" ? "需报告" : "reported"}</td>
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
  const { language } = useI18n();
  return (
    <section className="inspector-section trace-inspector" id="trace" aria-labelledby="trace-title">
      <div className="inspector-title">
        <div><span>{language === "zh" ? "审计事件回放" : "AUDIT EVENT REPLAY"}</span><h2 id="trace-title">{language === "zh" ? "控制平面审计" : "Control-plane audit"}</h2></div>
        <span className="tiny-status"><span className="status-dot running" />{trace.length} {language === "zh" ? "个事件" : "events"}</span>
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
  const { t } = useI18n();
  const proposal = task.memoryProposal;
  return (
    <section className="inspector-section memory-section" id="memory" aria-labelledby="memory-title">
      <div className="inspector-title">
          <div><span>{t("memory.flow")}</span><h2 id="memory-title">{t("memory.title")}</h2></div>
      </div>
      {proposal ? (
        <div className="memory-flow">
          <div className="memory-observation">
            <span>{t("memory.observation")}</span>
            <strong>{proposal.title}</strong>
            <p>{proposal.observation}</p>
          </div>
          <div className="promotion-line"><span /><ArrowRight size={13} /></div>
          <div className="skill-candidate">
            <span>{t("memory.skillCandidate")}</span>
            <div><strong>{proposal.candidateSkill}</strong><code>{proposal.version}</code></div>
            <p>{t("memory.outcomes", { count: proposal.supportCount })}</p>
            <div className="promotion-meter"><span style={{ width: `${Math.min(100, (proposal.supportCount / 3) * 100)}%` }} /></div>
          </div>
          <div className="promotion-hold"><ShieldCheck size={13} /> {t("memory.humanGated")}</div>
        </div>
      ) : (
        <InlineEmpty icon={GitBranch} text="No procedure is eligible for skill review." />
      )}
    </section>
  );
}

function IntegrationPanel({ integrations }: { integrations: IntegrationTruth[] }) {
  const { language, status } = useI18n();
  return (
    <section className="inspector-section integration-section" id="integrations" aria-labelledby="integration-title">
      <div className="inspector-title">
        <div><span>{language === "zh" ? "连接声明" : "CONNECTION CLAIMS"}</span><h2 id="integration-title">{language === "zh" ? "集成事实" : "Integration truth"}</h2></div>
      </div>
      <p className="truth-note">{language === "zh" ? "只有“已连接”的条目代表此运行栈中经过验证的端点。" : "Only “connected” rows represent a verified endpoint in this running stack."}</p>
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
              <span className={`truth-state ${item.status}`}>{status(item.status)}</span>
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
  const { language, status, t } = useI18n();
  return (
    <motion.section
      key={item.id}
      className="evidence-inspector"
      initial={{ opacity: 0, x: 16 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 12 }}
      aria-labelledby="evidence-inspector-title"
    >
      <button className="inspector-close" onClick={onClose} aria-label={language === "zh" ? "关闭证据详情" : "Close evidence inspector"}><X size={16} /></button>
      <span className="evidence-kind detail-kind">{item.kind.replaceAll("_", " ")}</span>
      <h2 id="evidence-inspector-title">{item.label}</h2>
      <p className="detail-intro">{language === "zh" ? "来自所选运行时的证据元数据。静态回放记录是明确标记的合成样例；任何生成式总结都不能替代底层产物。" : "Evidence metadata from the selected runtime. Static replay records are illustrative SYNTHETIC fixtures; no generated summary substitutes for an underlying artifact."}</p>
      <dl className="detail-list">
        <div><dt>{language === "zh" ? "状态" : "Status"}</dt><dd><span className={`ledger-status ${item.status}`}><StatusDot status={item.status} />{status(item.status)}</span></dd></div>
        <div><dt>{language === "zh" ? "摘要" : "Digest"}</dt><dd><code>{item.digest ?? t("common.notEmitted")}</code></dd></div>
        <div><dt>{language === "zh" ? "来源" : "Source"}</dt><dd>{item.source ?? t("common.notEmitted")}</dd></div>
        <div><dt>{language === "zh" ? "验证者" : "Verified by"}</dt><dd>{item.verifiedBy ?? (language === "zh" ? "未独立验证" : "not independently verified")}</dd></div>
        <div><dt>{language === "zh" ? "记录时间" : "Recorded at"}</dt><dd>{item.createdAt ?? t("common.notEmitted")}</dd></div>
        <div><dt>{language === "zh" ? "证据 ID" : "Evidence ID"}</dt><dd><code>{item.id}</code></dd></div>
      </dl>
      {item.raw && (
        <details className="raw-payload">
          <summary>{language === "zh" ? "原始证据负载" : "Raw evidence payload"} <ChevronDown size={13} /></summary>
          <pre>{JSON.stringify(item.raw, null, 2)}</pre>
        </details>
      )}
      <div className="inspector-rule">
        <ShieldCheck size={15} />
        <p><strong>{language === "zh" ? "决策不变量" : "Decision invariant"}</strong> {language === "zh" ? "必需证据缺失或未经验证时，决策门保持关闭。" : "Missing or unverified required evidence keeps the decision gate closed."}</p>
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
  const { language, stage, t } = useI18n();
  const approvalBlocked = task.stage === "APPROVAL" && !approvalToken;
  const operatorLocked = runtimeMode === "local_api" && !operatorConnected;
  const terminal = task.stage === "COMPLETED";
  return (
    <div className="action-dock" aria-label="Task controls">
      <div className="dock-state">
        <span className="status-dot running" />
        <div><small>{runtimeMode === "static_replay" ? t("runtime.browserReplay") : (language === "zh" ? "控制平面" : "CONTROL PLANE")}</small><strong>{stage(task.stage)}</strong></div>
      </div>
      <div className="dock-actions">
        <button className="button ghost" onClick={onReset} disabled={Boolean(busy) || operatorLocked}>
          <RotateCcw size={13} />{busy === "reset" ? t("action.resetting") : t("action.reset")}
        </button>
        <button className="button secondary" onClick={onAdvance} disabled={Boolean(busy) || operatorLocked || approvalBlocked || terminal}>
          {busy === "advance" ? t("action.advancing") : t("action.advance")}
        </button>
        <button className="button primary" onClick={onAutorun} disabled={Boolean(busy) || operatorLocked || approvalBlocked || terminal}>
          <Play size={13} fill="currentColor" />{busy === "autorun" ? t("action.running") : t("action.run")}
        </button>
      </div>
      {(operatorLocked || approvalBlocked) && (
        <span className="dock-hint">
          {operatorLocked
            ? (language === "zh" ? "连接操作者会话后才能执行写操作" : "Connect operator session to mutate")
            : runtimeMode === "static_replay" ? (language === "zh" ? "需要合成浏览器授权" : "Synthetic browser grant required") : (language === "zh" ? "需要审批令牌" : "Approval token required")}
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
  const { t } = useI18n();
  return (
    <div className="state-screen loading-screen" role="status">
      <NoiseField />
      <div className="loading-brand"><span className="brand-glyph">E</span><strong>EgoAgentOS</strong></div>
      <div className="loader-line"><span /></div>
      <p>{t("common.loading")}</p>
      <div className="skeleton-lines" aria-hidden="true"><i /><i /><i /></div>
    </div>
  );
}

function ErrorScreen({ message, onRetry, onFixture }: { message: string; onRetry: () => void; onFixture: () => void }) {
  const { language, t } = useI18n();
  return (
    <div className="state-screen error-screen">
      <NoiseField />
      <div className="state-icon"><CircleAlert size={23} /></div>
      <span className="eyebrow">{language === "zh" ? "控制平面不可用" : "CONTROL PLANE UNAVAILABLE"}</span>
      <h1>{language === "zh" ? "科研控制台无法建立已验证的数据链路。" : "The cockpit could not establish a verified data path."}</h1>
      <p>{message}</p>
      <div className="state-actions">
        <button className="button primary" onClick={onRetry}><RefreshCcw size={14} />{t("action.retry")}</button>
        <button className="button secondary" onClick={onFixture}>{t("action.openFixture")}</button>
      </div>
      <small>{language === "zh" ? "本地样例是合成数据，并会关闭自动刷新。" : "The local fixture is synthetic and disables automatic refresh."}</small>
    </div>
  );
}

function EmptyScreen({ onReset }: { onReset: () => void }) {
  const { language, t } = useI18n();
  return (
    <div className="state-screen empty-screen">
      <NoiseField />
      <div className="state-icon"><Database size={22} /></div>
      <span className="eyebrow">{language === "zh" ? "没有活动研究任务" : "NO ACTIVE RESEARCH TASK"}</span>
      <h1>{language === "zh" ? "控制平面已就绪，但任务账本为空。" : "The control plane is ready, but the task ledger is empty."}</h1>
      <p>{language === "zh" ? "恢复受控的合成场景，以检查完整的证据门禁工作流。" : "Restore the bounded synthetic scenario to inspect the complete evidence-gated workflow."}</p>
      <button className="button primary" onClick={onReset}><RotateCcw size={14} />{t("action.reset")}</button>
    </div>
  );
}

export default App;
export { approvalTokenForGeneration, EvidenceLedger, RXPProtocolView, StageSpine };
