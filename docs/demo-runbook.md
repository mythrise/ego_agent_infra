# Judge replay runbook

This is the semifinal replay path for the repository snapshot dated 2026-09-02. The
static replay does not require a GPU or cloud credentials. A separate official
AgentTeams `LIVE_LOCAL` path is documented below and deliberately stops before GPU.

## 0. Truth boundary before the demo

| Surface | What this replay proves | What it does not prove |
|---|---|---|
| Research Cockpit | persisted state, approval, evidence gate, audit, deterministic replay, and a bounded real-GPU workload contract | an actually executed GPU/model result |
| Fashion-MNIST adapter | real one-GPU FP32/AMP execution and offline-verifier contracts, covered by 13 tests | authenticated AgentTeams/GPU origin or a model-improvement result |
| Acceptance bundle | eight-scenario content-addressed Matrix/receipt/metric/gate/recovery/Trace/Decision checks, covered by 16 tests | live origin promotion or the full 14-scenario release gate |
| RXP API | schema catalog, synthetic fixture, structural ledger verification | RXP persistence in the task store or issuer trust |
| Skill API | six packages discovered, three deterministic handlers, digest-bound traces | durable rollout state or Nacos publication |
| PostgreSQL | real PostgreSQL 16 store/role/ledger contract, 32/32 integration tests | PolarDB cloud deployment or PITR |
| AgentTeams | static replay proves only the bridge contract; the separate local-live acceptance proves official Controller/Manager, four Worker resources, Matrix and Bridge connectivity | a physical GPU run or public hosted Controller |

## 1. Start the local stack

The intended one-command judge path is:

```bash
cp .env.example .env
# Run `openssl rand -hex 32` five times and paste distinct outputs into
# EGO_POSTGRES_PASSWORD, EGO_RUNTIME_PASSWORD,
# EGO_AGENTTEAMS_RUNTIME_PASSWORD, EGO_OPERATOR_KEY, and
# EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY.
# Set EGO_ALLOW_UNAUTHENTICATED_DEMO=true only for this labelled browser
# synthetic replay. Then run:
docker compose up --build
```

Open the cockpit at <http://localhost:4173>, OpenAPI at
<http://localhost:8000/docs>, and health at
<http://localhost:8000/api/v1/health>.

If the shell sends loopback traffic through a proxy, bypass it before the local curl
checks:

```bash
export NO_PROXY=127.0.0.1,localhost
export no_proxy=127.0.0.1,localhost
```

Evidence boundary: on 2026-09-02 the full local Compose stack passed PostgreSQL, API,
Web and Bridge health checks. The official AgentTeams path additionally passed four
distinct Matrix Agent replies. The native fallback remains useful when only the
deterministic replay is needed:

```bash
uv sync --python 3.9 --extra dev
set -a
. ./.env
set +a
export EGO_OPERATOR_ID=local.judge
export EGO_ALLOW_UNAUTHENTICATED_DEMO=true
EGO_DB_PATH=/tmp/egoagentos-judge.sqlite3 \
  uv run --python 3.9 --extra dev uvicorn apps.api.main:app \
  --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
npm --prefix apps/web ci
VITE_API_ROOT=http://127.0.0.1:8000/api/v1 \
  npm --prefix apps/web run dev -- --port 4173
```

To let the judge run the four live model experts from the three-level input composer, add the
model-plane environment to the API terminal before starting Uvicorn. Enter the secret without
printing it:

```bash
export EGO_AGENT_MODEL_BASE_URL=https://apihub.agnes-ai.com/v1
export EGO_AGENT_MODEL=agnes-2.5-pro
read -s EGO_AGENT_MODEL_API_KEY
export EGO_AGENT_MODEL_API_KEY
```

Paste only `EGO_OPERATOR_KEY` into the browser's operator-session field. Never paste
`EGO_AGENT_MODEL_API_KEY` into the page. A completed live planning run must show four HTTP 200
model receipts, four per-Agent compact receipts, a valid event chain, a compiled matrix digest,
and `execution_started=false`. It must still show official AgentTeams, Matrix transport,
retrieval, and physical GPU as `NOT_RUN` unless separate receipts have been ingested.

## 1A. Deploy and verify official AgentTeams locally

```bash
python3 scripts/deploy_local_live_stack.py all
```

This pins official AgentTeams `v1.2.3`, configures `agnes-2.5-pro`, creates Team
`ego-researchops`, four Worker resources and L2 Human `ego-judge`, joins the Human to the
Team room, and creates paused Project `egoagentos-gpu-gated-v1`. It then starts the
PostgreSQL-backed EgoAgentOS API and AgentTeams Bridge. The generated public receipt is
`.runtime/live-stack-public.json`; raw credentials stay only in the mode-0600
`.runtime/live-stack.env`.

Do not resume the workflow until a GPU host has been supplied and independently reviewed.
The 2026-09-02 local run reports `leaderReady=true`, three ready subordinate Workers and
four running Worker resources in total. The Matrix smoke received messages from all four
Agent identities. See [the frozen acceptance note](acceptance/live-local-2026-09-02.md).

## 2. Run the six-minute cockpit replay

1. Open the task cockpit for `ego-lite-001`; confirm the `SYNTHETIC DEMO` marker.
2. Reset, then choose “Run to next gate”. Deterministic role handlers advance the
   persisted task to APPROVAL and attribute each audit event to its identity contract.
3. Inspect the R2 approval: exact action digest, modeled GPU-hours, expiry, and rollback
   pointer. Advance controls are disabled while no scoped token exists.
4. Approve the displayed digest. The raw token is returned once and held only in the
   current browser session; automated policy tests cover invalid/scope/replay rejection.
5. Continue the replay. Inspect the explicitly synthetic low-GPU/high-CPU samples and
   the recorded manifest-based diagnosis. Do not describe this as a live MCP or GPU call.
6. Inspect raw baseline/candidate metric artifacts and deterministic comparison.
7. Open the evidence ledger and control-plane audit. A 7/7 count alone remains `HOLD`
   until the backend gate runs and the independent review passes; only then is the fixed
   local Decision committed.
8. Advance through DECIDE/ARCHIVE/MEMORY_SKILL. Inspect the validated failure/procedure;
   the Skill is a candidate/draft, not falsely marked published.
9. Reset and replay; confirm a new generation and a clean, generation-scoped event
   stream. The test suite separately proves canonical hash determinism.

## 3. Prove the RXP HTTP boundary

```bash
curl --silent --show-error --fail \
  http://127.0.0.1:8000/api/v1/rxp/schemas | jq

curl --silent --show-error --fail \
  http://127.0.0.1:8000/api/v1/rxp/demo > /tmp/ego-rxp-demo.json
jq '{ledger: .ledger}' /tmp/ego-rxp-demo.json > /tmp/ego-rxp-verify.json

curl --silent --show-error --fail \
  -X POST http://127.0.0.1:8000/api/v1/rxp/verify \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/ego-rxp-verify.json | jq
```

Expected truth: the demo says `physical_gpu_run=false`,
`production_signature_trust=false`, and `structural_verification=PASS`. Verification
returns `verified=true` but `signature_trust_verified=false`. The response is not
written into the ResearchOps task store.

## 4. Prove the Skill runtime boundary

Fetch the catalog and build a digest-pinned request from the returned descriptor:

```bash
# Load the same ignored operator key used by the API without printing it.
set -a
. ./.env
set +a

curl --silent --show-error --fail \
  http://127.0.0.1:8000/api/v1/skills > /tmp/ego-skills.json
jq '{total, executable, items: [.items[] | {name, version, package_digest, executable}]}' \
  /tmp/ego-skills.json

skill_version=$(jq -r '.items[] | select(.name == "dataset-manifest") | .version' \
  /tmp/ego-skills.json)
skill_digest=$(jq -r '.items[] | select(.name == "dataset-manifest") | .package_digest' \
  /tmp/ego-skills.json)

jq -n --arg version "$skill_version" --arg digest "$skill_digest" '{
  correlation_id: "judge-skill-001",
  expected_version: $version,
  expected_package_digest: $digest,
  payload: {
    dataset_id: "synthetic-judge-fixture",
    version: "1.0.0",
    files: [{
      path: "samples/manifest.txt",
      size: 12,
      sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }]
  }
}' > /tmp/ego-skill-request.json

curl --silent --show-error --fail \
  -X POST http://127.0.0.1:8000/api/v1/skills/dataset-manifest/invoke \
  -H "Authorization: Bearer $EGO_OPERATOR_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/ego-skill-request.json \
  | tee /tmp/ego-skill-invocation.json | jq

skill_invocation_id=$(jq -r '.trace.invocation_id' /tmp/ego-skill-invocation.json)
curl --silent --show-error --fail \
  "http://127.0.0.1:8000/api/v1/skill-invocations/$skill_invocation_id" | jq
```

Expected truth: the catalog reports `total=6` and `executable=3`; the invocation trace
binds the same correlation ID, version, package digest, input digest, and output digest.
`safe-experiment-runner` remains discovery-only in this API and fails closed with 403.

## 5. Replay the benchmark and test evidence

The committed local artifact used 5 repetitions across 14 scenarios and 3 profiles,
for 210 raw trials:

```bash
make benchmark
```

Its semantic result digest is
`05cab481a525210026d07377bb841ca0cd73f27790e9856b3c29211320b6b996`.
The deterministic core recorded 50 PASS and 20 capability SKIP trials. The scripted
negative control recorded 70 deliberate FAIL trials. The AgentTeams per-scenario harness
is unimplemented, so all 70 target trials are `SKIP`, never PASS; live opt-in does not
upgrade them.

The dated default-suite snapshot is 242 tests: API 69, RXP 26, Skills 6, semifinal proof 3,
Benchmark 29, Acceptance 16, AgentTeams 41, Experiments 13, MCP 23, and Web 16. Re-run
instead of treating that number as permanent:

```bash
make test
```

For the real PostgreSQL contract, point only at an explicit disposable test database:

```bash
EGO_TEST_POSTGRES_URL='postgresql://USER:PASSWORD@127.0.0.1:5432/TEST_DB' \
  make test-postgres
```

The suite recreates the `public` schema of that named test database. The verified
2026-08-29 result was 32/32 PASS on PostgreSQL 16.14. PolarDB and PITR were NOT RUN.
Those 32 tests cover control-plane and AgentTeams-bridge persistence, roles/RLS,
candidate-only memory curation, database-enforced append-only ledgers, durable cursors,
restart/CAS/idempotency, migration checksums, preflight contracts, and `LISTEN/NOTIFY`.

## 6. Optional live integration checks

Configure only services actually available to the team. The local integration route
reports metadata as `not_configured` or `configured_unverified`; it performs no
handshake. The MCP servers support stdio and an explicit loopback Streamable HTTP
smoke. Neither result makes AgentTeams, Higress, Nacos, cloud, or a GPU scheduler live.

The AgentTeams benchmark target remains `UNIMPLEMENTED/SKIP` until a version-matched
Controller, ready Team/Workers, Matrix credentials, a non-synthetic task, and the actual
per-scenario fault/replay harness all exist. A future PASS requires replayable
scenario-specific trace bundles, not a generic completion flag.
