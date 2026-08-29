import type { RXPProtocolData } from "./types";

export const syntheticRXP: RXPProtocolData = {
  protocol: "RXP/1.0",
  executionClass: "synthetic deterministic fixture",
  physicalGpuRun: false,
  productionSignatureTrust: false,
  fixtureSignatureVerified: true,
  structuralVerification: "PASS",
  verificationNotice:
    "Static replay of the committed fixture. The public demo key proves wiring only; no API verifier, AgentTeams service, or GPU job runs in this browser session.",
  matrixId: "matrix:synthetic-ablation-v1",
  completeness: "COMPLETE",
  expectedCellCount: 2,
  decidedCellCount: 2,
  missingDecisions: [],
  entryCount: 23,
  root: "sha256:2e313a284dcaaa6542d9d81919fc22bb61ab2015e18659f2dc0d323cbad47fd3",
  canonicalSha256: "sha256:178a24b303f13a480262498cd793fba6fe63570ceedb27928805b7c321362524",
  cells: [
    {
      cellId: "cell-baseline",
      state: "DECIDED",
      determinismLevel: "D3_BYTE_REPLAY_VERIFIED",
      intentDigest: "sha256:8c0752e3971521467cbc7311f236a5e1eb8728098ee3d08dede99eca3f7e74f8",
      grantDigest: "sha256:6c69421cd93c695fcfea3aa3328705a632c87c727c75ae0365273f4a276ef5eb",
      receiptDigest: "sha256:16f2436a7efcfda82730d318f96b7f75c8d5f6d2bbd1c5fe36f9c83cf948ba78",
      decisionDigest: "sha256:3cd0add481983754e8ac9d9dd42c47baad9ea71f6b0ab3678a6ed4c5a0d546fd",
      evidenceCount: 7,
    },
    {
      cellId: "cell-candidate",
      state: "DECIDED",
      determinismLevel: "D3_BYTE_REPLAY_VERIFIED",
      intentDigest: "sha256:85ae16129ebc93fc5a7e2dae76983cfe468a42bbda421d12e67f27159395ddc8",
      grantDigest: "sha256:80ca6b7f7300d7631eeb535cb7353594ae26deecaaace6305ca3e59d4a12cbb2",
      receiptDigest: "sha256:966fa737a96580a0c4bf1575c79c7400f68dc2767aaf5640b1692bd997209e10",
      decisionDigest: "sha256:4542249dd90d55dcef0005d0976dfab3d495855faa0f31b6e91d7fa5d6a3273d",
      evidenceCount: 7,
    },
  ],
};
