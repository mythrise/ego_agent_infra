import { useSyncExternalStore } from "react";

export const LANGUAGE_STORAGE_KEY = "egoagentos.language";

export type Language = "en" | "zh";
export type HtmlLanguage = "en" | "zh-CN";
export type TranslationParams = Record<string, string | number>;

const en = {
  "language.current": "English",
  "language.switch": "Switch language",
  "language.switchToEnglish": "Switch to English",
  "language.switchToChinese": "切换到中文",

  "nav.primary": "Primary navigation",
  "nav.open": "Open navigation",
  "nav.close": "Close navigation",
  "nav.compose": "Research composer",
  "nav.cockpit": "Task cockpit",
  "nav.acceptance": "Semifinal acceptance",
  "nav.experiments": "Experiments",
  "nav.protocol": "RXP protocol",
  "nav.evidence": "Evidence",
  "nav.trace": "Audit trace",
  "nav.integrations": "Integrations",
  "nav.github": "GitHub source",
  "nav.readme": "Read the README",
  "nav.notices": "Third-party notices",

  "runtime.browserReplay": "Browser replay",
  "runtime.noLiveServices": "no live services",
  "runtime.syntheticApi": "Synthetic API workspace",
  "runtime.staticBadge": "SYNTHETIC · STATIC REPLAY · NO API/MCP",
  "runtime.localBadge": "SYNTHETIC · LOCAL API",
  "runtime.staticBadgeShort": "STATIC · SYNTHETIC",
  "runtime.localBadgeShort": "API · SYNTHETIC",
  "runtime.updated": "UPDATED {time}",

  "composer.badge": "AGENT-NATIVE RESEARCH CONTROL PLANE",
  "composer.titleLine1": "From one research question",
  "composer.titleLine2": "to a decision you can replay.",
  "composer.lede": "AgentOS turns a complete protocol, a rough idea, or only a baseline into an explicit experiment tree—then binds every run, review, and memory update to evidence.",
  "composer.proof.stages": "sealed stages",
  "composer.proof.identity": "per-run identity",
  "composer.proof.memory": "isolated memory",
  "composer.inputLevel": "Research input level",
  "composer.level.detailed": "Full protocol",
  "composer.level.detailedDetail": "Plan + branches + code",
  "composer.level.idea": "Rough idea",
  "composer.level.ideaDetail": "Idea + frozen baseline",
  "composer.level.baseline": "Baseline only",
  "composer.level.baselineDetail": "Discover bounded branches",
  "composer.promptLabel": "Describe the next research move",
  "composer.promptPlaceholder": "Describe the baseline, hypothesis, constraints, and evidence needed to accept or reject the change.",
  "composer.browserExplainer": "BROWSER EXPLAINER · NO MODEL OR API CALL",
  "composer.localControlPlane": "LOCAL CONTROL PLANE · OPERATOR AUTH REQUIRED FOR MUTATIONS",
  "composer.compile": "Compile research tree",
  "composer.compiling": "Compiling…",
  "composer.chain.input": "INPUT",
  "composer.chain.tree": "TREE",
  "composer.chain.matrix": "MATRIX",
  "composer.chain.gate": "GATE",
  "composer.chain.compact": "COMPACT",
  "composer.chain.normalized": "normalized + hashed",
  "composer.chain.waiting": "waiting to compile",
  "composer.chain.treeValue": "Root · t · R · wrists",
  "composer.chain.cells": "{count} deterministic cells",
  "composer.chain.matrixFormula": "fold × seed × branch",
  "composer.chain.intent": "one intent token per cell",
  "composer.chain.evidence": "Evidence before decision",
  "composer.chain.veto": "reviewer can veto approval",
  "composer.chain.focus": "Agent / FOCUS.md",
  "composer.chain.context": "context {before} → {after}",
  "composer.chain.freshness": "per-stage freshness",

  "task.selected": "Selected research task",
  "task.activeGoal": "Active research goal",
  "task.completed": "Completed research task",
  "task.syntheticCheckpoint": "Synthetic replay checkpoint",
  "task.syntheticCompleted": "Completed synthetic replay",
  "task.acceptance": "Acceptance thresholds",
  "task.acceptanceSchema": "Acceptance schema",
  "task.notSupplied": "Not supplied",
  "task.policyClass": "Policy class",
  "task.approvalBounded": "approval bounded",
  "task.verifiedDecision": "Verified decision",
  "task.gateBound": "gate bound",

  "protocol.title": "Research eXecution Protocol",
  "protocol.note": "Experiment authority becomes a replayable causal chain",
  "protocol.structure": "STRUCTURE {status}",
  "protocol.staticVerifier": "STATIC FIXTURE · VERIFIER NOT EXECUTED HERE",
  "protocol.localVerifier": "LOCAL API · VERIFIER EXECUTED",
  "protocol.gpuRun": "GPU RUN · {status}",
  "protocol.signatureTrust": "PRODUCTION SIGNATURE TRUST · {status}",
  "protocol.frozenMatrix": "FROZEN MATRIX",
  "protocol.appendRoot": "APPEND-ONLY ROOT · {count} ENTRIES",
  "protocol.matrixCoverage": "MATRIX COVERAGE",
  "protocol.lifecycle": "RXP causal lifecycle",
  "protocol.intent": "Intent",
  "protocol.grant": "Grant",
  "protocol.receipt": "Receipt",
  "protocol.evidence": "Evidence",
  "protocol.decision": "Decision",
  "protocol.oneUseScope": "one-use scope",
  "protocol.merkleGate": "Merkle gate",
  "protocol.digestBound": "digest bound",
  "protocol.committedCells": "Committed matrix cells",
  "protocol.evidenceCount": "{count}/7 evidence",

  "acceptance.title": "Semifinal acceptance path",
  "acceptance.note": "Code-ready · external execution still evidence-gated",
  "acceptance.contract": "CONTRACT PATH IMPLEMENTED",
  "acceptance.externalOrigin": "EXTERNAL ORIGIN · UNVERIFIED",
  "acceptance.staticPage": "THIS PAGE · STATIC REPLAY",
  "acceptance.syntheticTask": "THIS TASK · SYNTHETIC API",
  "acceptance.layer": "Acceptance evidence layer",
  "acceptance.agentTeams": "AgentTeams + GPU",
  "acceptance.agentTeamsDetail": "controlled experiment chain",
  "acceptance.database": "Nexa + Agent Memory",
  "acceptance.databaseDetail": "authority and compact context",

  "action.reset": "Reset demo",
  "action.resetting": "Resetting…",
  "action.advance": "Advance once",
  "action.advancing": "Advancing…",
  "action.run": "Run to next gate",
  "action.running": "Running…",
  "action.approve": "Approve digest",
  "action.reject": "Reject",
  "action.recording": "Recording…",
  "action.refresh": "Refresh dashboard",
  "action.pauseRefresh": "Pause automatic refresh",
  "action.resumeRefresh": "Resume automatic refresh",
  "action.connect": "Connect session",
  "action.clearSession": "Clear session",
  "action.retry": "Retry connection",
  "action.openFixture": "Open labeled fixture",

  "status.pass": "PASS",
  "status.fail": "FAIL",
  "status.hold": "HOLD",
  "status.verified": "VERIFIED",
  "status.none": "NONE",
  "status.pending": "PENDING",
  "status.present": "PRESENT",
  "status.missing": "MISSING",
  "status.connected": "CONNECTED",
  "status.unconfigured": "NOT CONFIGURED",
  "status.simulated": "SIMULATED",
  "status.queued": "QUEUED",
  "status.blocked": "BLOCKED",
  "status.passed": "PASSED",
  "status.failed": "FAILED",
  "status.running": "RUNNING",
  "status.completed": "COMPLETED",
  "status.waitingForHuman": "WAITING FOR HUMAN",

  "stage.intake": "Intake",
  "stage.context": "Context",
  "stage.snapshot": "Snapshot",
  "stage.plan": "Plan",
  "stage.planReview": "Plan review",
  "stage.review": "Review",
  "stage.approval": "Approval",
  "stage.execute": "Execute",
  "stage.observe": "Observe",
  "stage.evaluate": "Evaluate",
  "stage.verify": "Verify",
  "stage.decide": "Decide",
  "stage.archive": "Archive",
  "stage.memory": "Memory",
  "stage.memorySkill": "Memory skill",
  "stage.keep": "Keep",
  "stage.reject": "Reject",
  "stage.completed": "Completed",

  "evidence.title": "Evidence ledger",
  "evidence.note": "SYNTHETIC artifacts before narrative claims",
  "evidence.decisionGate": "DECISION GATE",
  "evidence.artifactsPresent": "{present} / {required} artifacts present",
  "evidence.progress": "{percent} percent evidence complete",
  "evidence.inspect": "Inspect {label}",
  "evidence.sourceMissing": "source not emitted",
  "evidence.empty": "The evidence ledger is empty. A decision cannot be committed.",

  "memory.title": "Promotion candidate",
  "memory.flow": "EVIDENCE → MEMORY → SKILL",
  "memory.observation": "OBSERVATION",
  "memory.skillCandidate": "SKILL CANDIDATE",
  "memory.outcomes": "{count}/3 independently verified outcomes",
  "memory.humanGated": "Promotion remains human-gated",

  "common.notEmitted": "not emitted",
  "common.notRun": "not run",
  "common.unknown": "unknown",
  "common.loading": "Preparing the ResearchOps runtime…",
} as const;

export type TranslationKey = keyof typeof en;

const zh: Record<TranslationKey, string> = {
  "language.current": "中文",
  "language.switch": "切换语言",
  "language.switchToEnglish": "Switch to English",
  "language.switchToChinese": "切换到中文",

  "nav.primary": "主导航",
  "nav.open": "打开导航",
  "nav.close": "关闭导航",
  "nav.compose": "研究编排器",
  "nav.cockpit": "任务驾驶舱",
  "nav.acceptance": "复赛验收",
  "nav.experiments": "实验矩阵",
  "nav.protocol": "RXP 协议",
  "nav.evidence": "证据账本",
  "nav.trace": "审计轨迹",
  "nav.integrations": "系统集成",
  "nav.github": "GitHub 源码",
  "nav.readme": "阅读 README",
  "nav.notices": "第三方声明",

  "runtime.browserReplay": "浏览器回放",
  "runtime.noLiveServices": "未连接实时服务",
  "runtime.syntheticApi": "合成 API 工作区",
  "runtime.staticBadge": "合成数据 · 静态回放 · 无 API/MCP",
  "runtime.localBadge": "合成数据 · 本地 API",
  "runtime.staticBadgeShort": "静态 · 合成",
  "runtime.localBadgeShort": "API · 合成",
  "runtime.updated": "更新于 {time}",

  "composer.badge": "AGENT 原生科研控制平面",
  "composer.titleLine1": "从一个研究问题出发",
  "composer.titleLine2": "得到可重放的确定性决策。",
  "composer.lede": "AgentOS 将完整方案、模糊想法或单一 baseline 转换为显式实验树，并将每次运行、审查和记忆更新绑定到证据。",
  "composer.proof.stages": "封闭阶段",
  "composer.proof.identity": "单次运行身份",
  "composer.proof.memory": "隔离记忆",
  "composer.inputLevel": "研究输入层级",
  "composer.level.detailed": "完整方案",
  "composer.level.detailedDetail": "计划 + 支线 + 代码",
  "composer.level.idea": "模糊想法",
  "composer.level.ideaDetail": "想法 + 冻结 baseline",
  "composer.level.baseline": "只有 baseline",
  "composer.level.baselineDetail": "发现受控实验支线",
  "composer.promptLabel": "描述下一步研究动作",
  "composer.promptPlaceholder": "说明 baseline、假设、约束，以及接受或拒绝改进所需的证据。",
  "composer.browserExplainer": "浏览器说明模式 · 不调用模型或 API",
  "composer.localControlPlane": "本地控制平面 · 写操作需要操作者认证",
  "composer.compile": "编译研究树",
  "composer.compiling": "正在编译…",
  "composer.chain.input": "输入",
  "composer.chain.tree": "实验树",
  "composer.chain.matrix": "矩阵",
  "composer.chain.gate": "门禁",
  "composer.chain.compact": "压缩",
  "composer.chain.normalized": "已标准化并哈希",
  "composer.chain.waiting": "等待编译",
  "composer.chain.treeValue": "根节点 · t · R · 腕部",
  "composer.chain.cells": "{count} 个确定性单元",
  "composer.chain.matrixFormula": "fold × seed × 支线",
  "composer.chain.intent": "每个单元一个意图令牌",
  "composer.chain.evidence": "先有证据，再做决策",
  "composer.chain.veto": "审查员可否决已批准任务",
  "composer.chain.focus": "Agent / FOCUS.md",
  "composer.chain.context": "上下文 {before} → {after}",
  "composer.chain.freshness": "每阶段刷新注意力",

  "task.selected": "已选研究任务",
  "task.activeGoal": "进行中的研究目标",
  "task.completed": "已完成研究任务",
  "task.syntheticCheckpoint": "合成回放检查点",
  "task.syntheticCompleted": "已完成合成回放",
  "task.acceptance": "验收阈值",
  "task.acceptanceSchema": "验收规则",
  "task.notSupplied": "未提供",
  "task.policyClass": "策略等级",
  "task.approvalBounded": "受审批约束",
  "task.verifiedDecision": "已验证决策",
  "task.gateBound": "受门禁约束",

  "protocol.title": "研究执行协议",
  "protocol.note": "将实验权限固化为可重放的因果链",
  "protocol.structure": "结构 {status}",
  "protocol.staticVerifier": "静态样例 · 此处未运行验证器",
  "protocol.localVerifier": "本地 API · 已运行验证器",
  "protocol.gpuRun": "GPU 运行 · {status}",
  "protocol.signatureTrust": "生产签名信任 · {status}",
  "protocol.frozenMatrix": "冻结实验矩阵",
  "protocol.appendRoot": "只追加根 · {count} 条记录",
  "protocol.matrixCoverage": "矩阵覆盖率",
  "protocol.lifecycle": "RXP 因果生命周期",
  "protocol.intent": "意图",
  "protocol.grant": "授权",
  "protocol.receipt": "回执",
  "protocol.evidence": "证据",
  "protocol.decision": "决策",
  "protocol.oneUseScope": "一次性权限",
  "protocol.merkleGate": "Merkle 门禁",
  "protocol.digestBound": "摘要绑定",
  "protocol.committedCells": "已提交的矩阵单元",
  "protocol.evidenceCount": "{count}/7 份证据",

  "acceptance.title": "复赛验收链路",
  "acceptance.note": "代码就绪 · 外部执行仍受证据门禁约束",
  "acceptance.contract": "协议链路已实现",
  "acceptance.externalOrigin": "外部来源 · 未验证",
  "acceptance.staticPage": "当前页面 · 静态回放",
  "acceptance.syntheticTask": "当前任务 · 合成 API",
  "acceptance.layer": "验收证据层",
  "acceptance.agentTeams": "AgentTeams + GPU",
  "acceptance.agentTeamsDetail": "受控实验链",
  "acceptance.database": "Nexa + Agent Memory",
  "acceptance.databaseDetail": "权限与精简上下文",

  "action.reset": "重置演示",
  "action.resetting": "正在重置…",
  "action.advance": "推进一次",
  "action.advancing": "正在推进…",
  "action.run": "运行到下一门禁",
  "action.running": "正在运行…",
  "action.approve": "批准摘要",
  "action.reject": "拒绝",
  "action.recording": "正在记录…",
  "action.refresh": "刷新面板",
  "action.pauseRefresh": "暂停自动刷新",
  "action.resumeRefresh": "恢复自动刷新",
  "action.connect": "连接会话",
  "action.clearSession": "清除会话",
  "action.retry": "重试连接",
  "action.openFixture": "打开标记样例",

  "status.pass": "通过",
  "status.fail": "失败",
  "status.hold": "暂停",
  "status.verified": "已验证",
  "status.none": "无",
  "status.pending": "待处理",
  "status.present": "已存在",
  "status.missing": "缺失",
  "status.connected": "已连接",
  "status.unconfigured": "未配置",
  "status.simulated": "模拟",
  "status.queued": "排队中",
  "status.blocked": "已阻断",
  "status.passed": "已通过",
  "status.failed": "失败",
  "status.running": "运行中",
  "status.completed": "已完成",
  "status.waitingForHuman": "等待人工操作",

  "stage.intake": "接收",
  "stage.context": "上下文",
  "stage.snapshot": "快照",
  "stage.plan": "规划",
  "stage.planReview": "方案审查",
  "stage.review": "审查",
  "stage.approval": "审批",
  "stage.execute": "执行",
  "stage.observe": "观测",
  "stage.evaluate": "评测",
  "stage.verify": "验证",
  "stage.decide": "决策",
  "stage.archive": "归档",
  "stage.memory": "记忆",
  "stage.memorySkill": "记忆技能",
  "stage.keep": "保留",
  "stage.reject": "拒绝",
  "stage.completed": "完成",

  "evidence.title": "证据账本",
  "evidence.note": "在叙事性结论之前冻结合成证据",
  "evidence.decisionGate": "决策门禁",
  "evidence.artifactsPresent": "已具备 {present} / {required} 份证据",
  "evidence.progress": "证据完成度 {percent}%",
  "evidence.inspect": "查看 {label}",
  "evidence.sourceMissing": "未产生来源信息",
  "evidence.empty": "证据账本为空，无法提交决策。",

  "memory.title": "晋升候选",
  "memory.flow": "证据 → 记忆 → 技能",
  "memory.observation": "观察",
  "memory.skillCandidate": "技能候选",
  "memory.outcomes": "{count}/3 个独立验证结果",
  "memory.humanGated": "晋升仍需人工审批",

  "common.notEmitted": "未产生",
  "common.notRun": "未运行",
  "common.unknown": "未知",
  "common.loading": "正在准备 ResearchOps 运行环境…",
};

export const translations: Readonly<Record<Language, Readonly<Record<TranslationKey, string>>>> = {
  en,
  zh,
};

const stageKeys = {
  INTAKE: "stage.intake",
  CONTEXT: "stage.context",
  SNAPSHOT: "stage.snapshot",
  PLAN: "stage.plan",
  PLAN_REVIEW: "stage.planReview",
  REVIEW: "stage.review",
  APPROVAL: "stage.approval",
  EXECUTE: "stage.execute",
  OBSERVE: "stage.observe",
  EVALUATE: "stage.evaluate",
  VERIFY: "stage.verify",
  DECIDE: "stage.decide",
  ARCHIVE: "stage.archive",
  MEMORY: "stage.memory",
  MEMORY_SKILL: "stage.memorySkill",
  KEEP: "stage.keep",
  REJECT: "stage.reject",
  COMPLETED: "stage.completed",
} as const satisfies Record<string, TranslationKey>;

const statusKeys = {
  pass: "status.pass",
  fail: "status.fail",
  hold: "status.hold",
  verified: "status.verified",
  none: "status.none",
  pending: "status.pending",
  present: "status.present",
  missing: "status.missing",
  connected: "status.connected",
  unconfigured: "status.unconfigured",
  simulated: "status.simulated",
  queued: "status.queued",
  blocked: "status.blocked",
  passed: "status.passed",
  failed: "status.failed",
  running: "status.running",
  completed: "status.completed",
  waiting_for_human: "status.waitingForHuman",
} as const satisfies Record<string, TranslationKey>;

export type ResearchStage = keyof typeof stageKeys;
export type KnownStatus = keyof typeof statusKeys;

export interface I18nStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface I18nDocument {
  documentElement: { lang: string };
}

export interface I18nStoreOptions {
  initialLanguage?: Language;
  storage?: I18nStorage | null;
  document?: I18nDocument | null;
  listenForStorageChanges?: boolean;
}

export function normalizeLanguage(value: unknown): Language | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim().toLowerCase().replaceAll("_", "-");
  if (normalized === "zh" || normalized.startsWith("zh-")) return "zh";
  if (normalized === "en" || normalized.startsWith("en-")) return "en";
  return null;
}

export function htmlLanguage(language: Language): HtmlLanguage {
  return language === "zh" ? "zh-CN" : "en";
}

export function translate(
  language: Language,
  key: TranslationKey,
  params: TranslationParams = {},
): string {
  return translations[language][key].replace(/\{([a-zA-Z][\w-]*)\}/g, (placeholder, name: string) => {
    const value = params[name];
    return value === undefined ? placeholder : String(value);
  });
}

export function labelForStage(language: Language, stage: string): string {
  const key = stageKeys[stage.toUpperCase() as ResearchStage];
  return key ? translate(language, key) : stage.replaceAll("_", " ");
}

export function labelForStatus(language: Language, status: string): string {
  const normalized = status.trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
  const key = statusKeys[normalized as KnownStatus];
  return key ? translate(language, key) : status.replaceAll("_", " ");
}

export interface I18nStore {
  getLanguage(): Language;
  setLanguage(language: Language): void;
  toggleLanguage(): void;
  subscribe(listener: () => void): () => void;
  t(key: TranslationKey, params?: TranslationParams): string;
  stage(stage: string): string;
  status(status: string): string;
  destroy(): void;
}

function browserStorage(): I18nStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function browserDocument(): I18nDocument | null {
  return typeof document === "undefined" ? null : document;
}

export function createI18nStore(options: I18nStoreOptions = {}): I18nStore {
  const storage = options.storage === undefined ? browserStorage() : options.storage;
  const targetDocument = options.document === undefined ? browserDocument() : options.document;
  const listeners = new Set<() => void>();
  let language = options.initialLanguage ?? "en";

  try {
    language = normalizeLanguage(storage?.getItem(LANGUAGE_STORAGE_KEY)) ?? language;
  } catch {
    // A blocked storage backend must never make the interface unusable.
  }

  const applyDocumentLanguage = () => {
    if (targetDocument) targetDocument.documentElement.lang = htmlLanguage(language);
  };
  applyDocumentLanguage();

  const setLanguage = (nextLanguage: Language, persist = true) => {
    if (language === nextLanguage) {
      applyDocumentLanguage();
      return;
    }
    language = nextLanguage;
    applyDocumentLanguage();
    if (persist) {
      try {
        storage?.setItem(LANGUAGE_STORAGE_KEY, nextLanguage);
      } catch {
        // Persistence is progressive enhancement; translation remains available.
      }
    }
    listeners.forEach((listener) => listener());
  };

  const storageListener = (event: StorageEvent) => {
    if (event.key !== LANGUAGE_STORAGE_KEY) return;
    const nextLanguage = normalizeLanguage(event.newValue);
    if (nextLanguage) setLanguage(nextLanguage, false);
  };
  const listenForStorageChanges = options.listenForStorageChanges ?? options.storage === undefined;
  if (listenForStorageChanges && typeof window !== "undefined") {
    window.addEventListener("storage", storageListener);
  }

  return {
    getLanguage: () => language,
    setLanguage,
    toggleLanguage: () => setLanguage(language === "en" ? "zh" : "en"),
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    t: (key, params) => translate(language, key, params),
    stage: (stage) => labelForStage(language, stage),
    status: (status) => labelForStatus(language, status),
    destroy() {
      listeners.clear();
      if (listenForStorageChanges && typeof window !== "undefined") {
        window.removeEventListener("storage", storageListener);
      }
    },
  };
}

export const i18n = createI18nStore();

export function useI18n(store: I18nStore = i18n) {
  const language = useSyncExternalStore(store.subscribe, store.getLanguage, store.getLanguage);
  return {
    language,
    htmlLanguage: htmlLanguage(language),
    setLanguage: store.setLanguage,
    toggleLanguage: store.toggleLanguage,
    t: store.t,
    stage: store.stage,
    status: store.status,
  } as const;
}
