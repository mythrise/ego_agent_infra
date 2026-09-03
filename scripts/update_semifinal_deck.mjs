#!/usr/bin/env node
/** Update the inherited 16-slide semifinal deck with final acceptance facts. */

import process from "node:process";

const modulePath = process.env.ARTIFACT_TOOL_MODULE;
if (!modulePath) throw new Error("ARTIFACT_TOOL_MODULE is required");
const { FileBlob, PresentationFile } = await import(`file://${modulePath}`);

const starterPptx = process.argv[2];
const output = process.argv[3];
if (!starterPptx || !output) {
  throw new Error("usage: update_semifinal_deck.mjs INPUT.pptx OUTPUT.pptx");
}

const replacements = [
  [2, "AgentTeams + bounded GPU chain", "Official AgentTeams · 4 Worker resources"],
  [2, "bridge · recovery｜official live NOT RUN", "Controller / Manager / Team / Matrix · LIVE_LOCAL"],
  [2, "official AgentTeams · GPU · PolarDB/PITR 不冒认", "workflow / GPU / Nexa / PITR 不冒认"],
  [4, "contract + local PG 可重放；official AgentTeams / GPU / PolarDB / PITR = NOT RUN", "AgentTeams infra / Matrix = LIVE_LOCAL；workflow / GPU / Nexa / PITR = NOT RUN"],
  [6, "AgentTeams · Dynamic collaboration", "AgentTeams · LIVE_LOCAL infrastructure"],
  [6, "冲突、超时与失败会改变路由", "基础设施已联通；科研工作流仍冻结"],
  [6, "bridge + PostgreSQL contracts 已验证；official service / Matrix / GPU 未实跑，target 必须 SKIP", "Controller / Manager / 4 Workers / Matrix = LIVE_LOCAL；8 nodes PENDING；GPU NOT_ATTACHED"],
  [12, "Evidence · 2026-08-29 本地复测", "Evidence · 2026-09-03 最终验收"],
  [12, "默认 210 tests 全过；另有 PostgreSQL 16.14 / 27 tests。外部 live 项单列 NOT RUN。", "make test：530 PASS / 1 SKIP；full pytest：689 PASS / 1 SKIP；PostgreSQL：38 / 38。"],
  [12, "210", "689"],
  [12, "default suite PASS", "full pytest PASS"],
  [12, "API 56 · RXP 26 · Skills 6 · Proof 2 · Benchmark 28\nAcceptance 16 · AgentTeams 28 · Experiments 13 · MCP 23 · Web 12", "API 89 · RXP 26 · Skills 6 · Proof 3 · Benchmark 29\nAcceptance 16 · AgentTeams 264 · Experiments 16 · MCP 53 · Web 28"],
  [12, "12", "28"],
  [12, "28", "264"],
  [12, "27", "38"],
  [12, "NOT RUN", "PENDING"],
  [12, "external live", "workflow"],
  [12, "bridge/trace 与本地 PG 实库通过；official AgentTeams、GPU、PolarDB 不在此结果中。", "official infra / Matrix 已 LIVE_LOCAL；科研 workflow、GPU、Nexa / PITR 未运行。"],
  [12, "证据命令：make test · pytest tests/postgres · ego-semifinal-bundle verify · make package", "证据命令：make test · pytest tests/postgres · freeze_live_local_proof.py · make package"],
  [13, "checkpoint", "检查点"],
  [13, "SQLite local 与 PostgreSQL profile 都要求重启后读取 authoritative state。", "SQLite dev 与 PostgreSQL profile 都要求重启后读取 authoritative state。"],
  [14, "SQLite 仅作 dev fallback；PostgreSQL 是生产路径，承载并发、权限、通知与不可变账本。PolarDB 仍需云端实证。", "SQLite 仅作 dev fallback；PostgreSQL 生产语义已验证；TDSQL Nexa / Agent Memory 云端仍待实证。"],
  [14, "PostgreSQL 16.14 · 27 integration tests", "PostgreSQL 16 · 38 integration tests"],
  [14, "PolarDB · PITR · read/write split · cloud IAM", "TDSQL Nexa · Agent Memory · PITR · cloud IAM"],
  [14, "preflight PASS + restore / failover drill", "Nexa preflight + restore / failover drill"],
  [15, "PostgreSQL 16.14 + AgentTeams bridge contracts", "PostgreSQL 16 + AgentTeams LIVE_LOCAL"],
  [15, "PG 27 + bridge 28；账本篡改、receipt 重用、崩溃恢复与来源冒充均有负测。", "PG 38 + bridge 264；Controller / Manager / 4 Workers / Matrix smoke 已联通。"],
  [15, "仍为 SKIP", "仍未运行"],
  [15, "official AgentTeams · single GPU · PolarDB / PITR", "科研 workflow · GPU · Nexa / PITR"],
  [15, "没有可认证 origin、endpoint 与 same-run trace，就不把 contract 写成 live 成功。", "Project 的 8 nodes 均 PENDING；没有 GPU receipt 与 same-run trace 就不写成闭环成功。"],
  [15, "GitHub Pages 仅托管 static judge replay；API / AgentTeams / PostgreSQL capability 必须由本地或部署 profile 证明", "Pages 提供 LIVE_BROWSER 专家规划 + synthetic replay；AgentTeams / PostgreSQL 由 LIVE_LOCAL 证据证明"],
  [16, "AgentTeams → 人工审批 → bounded single-GPU contract。", "展示 AgentTeams LIVE_LOCAL、冻结 workflow 与 R2 授权合同。"],
  [16, "raw metrics → deterministic eval → reviewer → Decision。", "步进 synthetic run：raw metrics → eval → reviewer → Decision。"],
  [16, "PG roles / recovery、benchmark 与 NOT RUN boundary。", "PG 38/38、Matrix proof、recovery 与 NOT RUN boundary。"],
  [16, "make test · PostgreSQL 27 · acceptance bundle verify", "make test · PostgreSQL 38 · LIVE_LOCAL proof"],
];

const presentation = await PresentationFile.importPptx(await FileBlob.load(starterPptx));
let edits = 0;
const seen = new Set();

for (const slide of presentation.slides.items) {
  const slideNumber = slide.slideNumber;
  for (const shape of slide.shapes.items) {
    if (!shape.text) continue;
    const original = shape.text.toString();
    if (!original) continue;
    for (const [targetSlide, from, to] of replacements) {
      if (slideNumber !== targetSlide || original !== from) continue;
      shape.text.replace(from, to);
      edits += 1;
      seen.add(`${targetSlide}:${from}`);
    }
  }
}

const missing = replacements
  .map(([slide, from]) => `${slide}:${from}`)
  .filter((key) => !seen.has(key));
if (missing.length) {
  throw new Error(`required deck anchors missing: ${missing.join(" | ")}`);
}

const pptx = await PresentationFile.exportPptx(presentation);
await pptx.save(output);
console.log(JSON.stringify({ status: "PASS", edits, output }));
