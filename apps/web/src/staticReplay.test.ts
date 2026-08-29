import { describe, expect, it } from "vitest";
import { createStaticReplayApi } from "./staticReplay";

describe("browser-only deterministic replay", () => {
  it("stays visibly synthetic and reports no connected services", async () => {
    const replay = createStaticReplayApi();
    const dashboard = await replay.dashboard();

    expect(dashboard.runtimeMode).toBe("static_replay");
    expect(dashboard.demoMode).toBe(true);
    expect(dashboard.tasks[0].stage).toBe("APPROVAL");
    expect(dashboard.integrations.every((item) => item.status !== "connected")).toBe(true);
    expect(dashboard.integrations[0].detail).toMatch(/no backend api or mcp server/i);
  });

  it("enforces the R2 grant, holds at 7/7, then emits the synthetic KEEP fixture", async () => {
    const replay = createStaticReplayApi();
    const initial = await replay.dashboard();
    const taskId = initial.activeTaskId;
    const approval = initial.tasks[0].pendingApproval!;

    await expect(replay.advance(taskId)).rejects.toThrow(/browser grant is required/i);
    await expect(
      replay.decide(approval.id, {
        decision: "approved",
        expected_digest: "wrong-digest",
      }),
    ).rejects.toThrow(/digest does not match/i);

    const result = await replay.decide(approval.id, {
      decision: "approved",
      expected_digest: approval.expectedDigest,
    });
    expect(result.approval_token).toMatch(/^synthetic_replay_grant:/);

    const verify = await replay.autorun(taskId, result.approval_token);
    expect(verify.stage).toBe("VERIFY");
    expect(verify.evidencePresent).toBe(7);
    expect(verify.gateStatus).toBe("not_run");
    expect(verify.decision).toBeUndefined();

    const completed = await replay.autorun(taskId);
    expect(completed.stage).toBe("COMPLETED");
    expect(completed.gateStatus).toBe("pass");
    expect(completed.decision).toBe("KEEP");
  });

  it("invalidates an old browser grant when reset creates a new generation", async () => {
    const replay = createStaticReplayApi();
    const first = await replay.dashboard();
    const approval = first.tasks[0].pendingApproval!;
    const grant = await replay.decide(approval.id, {
      decision: "approved",
      expected_digest: approval.expectedDigest,
    });

    const reset = await replay.reset();
    await replay.autorun(reset.activeTaskId);
    await expect(replay.advance(reset.activeTaskId, grant.approval_token)).rejects.toThrow(/browser grant is required/i);
  });

  it("keeps APPROVAL pending when stepping there from a clean reset", async () => {
    const replay = createStaticReplayApi();
    const reset = await replay.reset();
    let task = reset.tasks[0];

    while (task.stage !== "APPROVAL") task = await replay.advance(task.id);
    expect(task.pendingApproval?.status).toBe("pending");

    const approval = task.pendingApproval!;
    const result = await replay.decide(approval.id, {
      decision: "approved",
      expected_digest: approval.expectedDigest,
    });
    const executing = await replay.advance(task.id, result.approval_token);
    expect(executing.stage).toBe("EXECUTE");
  });
});
