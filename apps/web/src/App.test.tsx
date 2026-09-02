import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App, { approvalTokenForGeneration, EvidenceLedger, ResearchComposer, RXPProtocolView, StageSpine } from "./App";
import { clearOperatorSession, researchApi } from "./api";
import { syntheticDashboard, syntheticTask } from "./demoData";
import { syntheticRXP } from "./rxpDemoData";

vi.mock("framer-motion", async () => {
  const React = await import("react");
  const passthrough = React.forwardRef<HTMLElement, React.HTMLAttributes<HTMLElement> & { children?: React.ReactNode }>(
    ({ children, ...props }, ref) => React.createElement("div", { ...props, ref }, children),
  );
  return {
    AnimatePresence: ({ children }: { children: React.ReactNode }) => children,
    motion: new Proxy({}, { get: () => passthrough }),
    useReducedMotion: () => true,
  };
});

afterEach(() => {
  clearOperatorSession();
  vi.restoreAllMocks();
});

describe("ResearchOps cockpit primitives", () => {
  it("accepts custom input for all three judge modes and never fakes a live run in static replay", async () => {
    render(<ResearchComposer runtimeMode="static_replay" />);

    const editor = screen.getByRole("textbox", { name: /paste a real project brief/i });
    expect(editor).toHaveValue("");
    expect(screen.getByText("Example input")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Use this example" }));
    expect((editor as HTMLTextAreaElement).value).toContain("C7 RTMW-G5");

    fireEvent.click(screen.getByRole("tab", { name: /rough idea/i }));
    expect(screen.getByRole("textbox", { name: /paste a real project brief/i })).toHaveValue("");
    fireEvent.change(screen.getByRole("textbox", { name: /paste a real project brief/i }), {
      target: { value: "Frozen baseline plus a bounded translation-residual idea." },
    });

    fireEvent.click(screen.getByRole("tab", { name: /baseline only/i }));
    expect(screen.getByRole("textbox", { name: /paste a real project brief/i })).toHaveValue("");
    fireEvent.click(screen.getByRole("tab", { name: /rough idea/i }));
    expect(screen.getByRole("textbox", { name: /paste a real project brief/i })).toHaveValue(
      "Frozen baseline plus a bounded translation-residual idea.",
    );

    fireEvent.click(screen.getByRole("button", { name: /run live expert team/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/requires the server-side api/i);
    expect(screen.getByText("BACKEND REQUIRED")).toBeInTheDocument();
  });

  it("marks the current deterministic workflow stage", () => {
    render(<StageSpine current="APPROVAL" reducedMotion />);

    expect(screen.getByText("APPROVAL").closest(".stage-node")).toHaveAttribute("aria-current", "step");
    expect(screen.getByLabelText(/workflow stage approval/i)).toBeInTheDocument();
  });

  it("opens an evidence item through the ledger affordance", () => {
    const onInspect = vi.fn();
    render(
      <EvidenceLedger
        evidence={syntheticTask.evidence}
        present={syntheticTask.evidencePresent}
        required={syntheticTask.evidenceRequired}
        gateStatus={syntheticTask.gateStatus}
        onInspect={onInspect}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /inspect frozen researchgoal/i }));
    expect(onInspect).toHaveBeenCalledWith(syntheticTask.evidence[0]);
    expect(screen.getByText("3 / 7 artifacts present")).toBeInTheDocument();
    expect(screen.getByText("HOLD")).toBeInTheDocument();
  });

  it("never reuses a one-time approval token across task generations", () => {
    const grant = { token: "one-time-token", generation: "gen-old" };
    expect(approvalTokenForGeneration(grant, "gen-old")).toBe("one-time-token");
    expect(approvalTokenForGeneration(grant, "gen-new")).toBeUndefined();
  });

  it("shows RXP matrix completeness and lets a judge inspect a committed cell", () => {
    render(<RXPProtocolView data={syntheticRXP} runtimeMode="static_replay" />);

    expect(screen.getByText("2/2 COMPLETE")).toBeInTheDocument();
    expect(screen.getByText(/verifier not executed here/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /cell-candidate/i }));
    expect(screen.getByTitle(syntheticRXP.cells[1].intentDigest)).toHaveTextContent(/sha256:/);
  });

  it("connects an in-memory operator, approves, then advances with the one-time token", async () => {
    const dashboard = structuredClone(syntheticDashboard);
    dashboard.runtimeMode = "local_api";
    dashboard.tasks = [structuredClone(syntheticTask)];
    dashboard.activeTaskId = syntheticTask.id;
    const dashboardSpy = vi.spyOn(researchApi, "dashboard").mockResolvedValue(dashboard);
    vi.spyOn(researchApi, "task").mockResolvedValue(structuredClone(syntheticTask));
    vi.spyOn(researchApi, "rxpDemo").mockResolvedValue(structuredClone(syntheticRXP));
    const decideSpy = vi.spyOn(researchApi, "decide").mockResolvedValue({
      approval_token: "one-time-header-token",
    });
    const advanceSpy = vi.spyOn(researchApi, "advance").mockResolvedValue({});

    render(<App />);

    const keyInput = await screen.findByLabelText("Operator session key");
    fireEvent.change(keyInput, { target: { value: "operator-key-kept-in-memory-only-123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect session" }));
    expect(await screen.findByText("OPERATOR CONNECTED")).toBeInTheDocument();

    const approve = screen.getByRole("button", { name: /approve digest/i });
    expect(approve).toBeEnabled();
    fireEvent.click(approve);
    await waitFor(() => expect(decideSpy).toHaveBeenCalledWith(
      syntheticTask.pendingApproval!.id,
      {
        decision: "approved",
        expected_digest: syntheticTask.pendingApproval!.expectedDigest,
      },
    ));

    const advance = screen.getByRole("button", { name: "Advance once" });
    await waitFor(() => expect(advance).toBeEnabled());
    fireEvent.click(advance);
    await waitFor(() => expect(advanceSpy).toHaveBeenCalledWith(
      syntheticTask.id,
      "one-time-header-token",
    ));
    expect(dashboardSpy).toHaveBeenCalled();
  });
});
