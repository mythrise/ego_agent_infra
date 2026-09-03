import { afterEach, describe, expect, it, vi } from "vitest";
import { defaultJudgeDemoConfig, runPublicExpertTeam } from "./publicExpertApi";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("public judge expert runner", () => {
  it("calls four live browser experts while keeping GPU and AgentTeams assumption-only", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      const request = JSON.parse(String(init?.body)) as {
        messages: Array<{ role: string; content: string }>;
      };
      const prompt = request.messages[0].content;
      const inputDigest = prompt.match(/input_digest MUST equal ([0-9a-f]{64})/)?.[1] ?? "";
      const reviewedDigest = prompt.match(/reviewed_digest MUST equal ([0-9a-f]{64})/)?.[1] ?? "";
      let output: Record<string, unknown>;
      if (prompt.includes("EgoAgentOS research-pi expert")) {
        output = {
          role: "research-pi",
          input_digest: inputDigest,
          normalized_title: "Bounded plan",
          normalized_objective: "Test one variable with fixed evidence requirements.",
          assumptions: ["GPU is an assumption"],
          success_criteria: ["The plan is reviewable"],
        };
      } else if (prompt.includes("EgoAgentOS scout expert")) {
        output = {
          role: "scout",
          input_digest: inputDigest,
          baseline_summary: "Frozen baseline",
          constraints: ["No execution claim"],
          uncertainties: ["No hardware receipt"],
          evidence_needs: ["Raw metric receipt"],
        };
      } else if (prompt.includes("EgoAgentOS experiment-architect expert")) {
        output = {
          role: "experiment-architect",
          input_digest: inputDigest,
          candidate_branches: ["baseline", "candidate"],
          metrics: ["accuracy"],
          folds: [0, 1],
          seeds: [7, 11],
          falsification_checks: ["identity control"],
          budget_assessment: "Two assumed GPU hours",
          recommendation: "Review before execution",
        };
      } else {
        output = {
          role: "reviewer",
          independent: true,
          reviewed_digest: reviewedDigest,
          verdict: "WARN",
          findings: ["Hardware remains assumed"],
          decision: "Plan is suitable for assumption review",
          claim_boundary: "No GPU or Matrix execution occurred",
        };
      }
      return new Response(JSON.stringify({
        id: `response-${fetchMock.mock.calls.length}`,
        model: "deepseek-v4-flash",
        choices: [{ message: { content: JSON.stringify(output) }, finish_reason: "stop" }],
        usage: { total_tokens: 120 },
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const updates: string[] = [];

    const result = await runPublicExpertTeam({
      input_mode: "idea",
      content: "Frozen baseline with one bounded improvement idea and a strict evidence gate.",
      locale: "en",
    }, {
      ...defaultJudgeDemoConfig,
      apiKey: "public-demo-key",
    }, (run) => updates.push(run.status));

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(result.status).toBe("completed");
    expect(result.roles.every((role) => role.status === "completed")).toBe(true);
    expect(result.roles.every((role) => role.receipt?.model === "deepseek-v4-flash")).toBe(true);
    expect(result.compile?.matrix_cell_count).toBe(8);
    expect(result.decision).toMatchObject({
      status: "PLAN_READY_FOR_ASSUMPTION_REVIEW",
      reviewer_verdict: "WARN",
      execution_started: false,
    });
    expect(result.truth_boundary).toMatchObject({
      external_model_calls: "LIVE_BROWSER",
      official_agentteams_controller: "ASSUMPTION_ONLY",
      matrix_transport: "ASSUMPTION_ONLY",
      physical_gpu: "ASSUMPTION_ONLY",
    });
    expect(result.event_chain_valid).toBe(true);
    expect(updates).toContain("running");

    const firstInit = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(firstInit.headers).get("Authorization")).toBe("Bearer public-demo-key");
  });
});
