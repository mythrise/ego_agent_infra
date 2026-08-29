# EgoAgentOS Skills

The six packages in this directory are reusable research operations capabilities,
not one-off prompts. Each package follows the portable Agent Skill layout expected
by Nacos Skill Registry (`SKILL.md` plus optional `scripts/`, `references/`, and
`assets/`). Only `name` and `description` are treated as portable frontmatter.
`egoagentos.skill.yaml` is an explicitly project-specific extension for risk,
version, idempotency, and evidence policy.

| Skill | Primary users | Deterministic boundary |
|---|---|---|
| `research-plan` | PI, Architect, Reviewer | goal/plan schema and budget checks |
| `dataset-manifest` | Scout, Runtime | canonical dataset manifest digest |
| `safe-experiment-runner` | Runtime | entrypoint allowlist, approval scope |
| `ablation-analyzer` | Evaluator, Reviewer | comparisons and fixed-seed bootstrap |
| `evidence-gate` | Reviewer, PI | required kinds, digests, independence |
| `research-memory` | Scout, Memory Curator | validated-only writes and ranking |

## Package discovery and execution are separate

`skill_runtime/registry.py` discovers all six packages, requires a strict numeric
SemVer `x.y.z`, checks distinct owner/reviewer identities, and computes a package
SHA-256 over the exact `SKILL.md` bytes plus the canonical manifest. A caller can pin
both `expected_version` and `expected_package_digest`; a mismatch fails closed and
still emits a correlated failure trace.

Three handlers are allowlisted and executable:

| Handler | Deterministic output |
|---|---|
| `research-plan` | frozen plan validation and `plan_digest` |
| `dataset-manifest` | traversal-safe sorted manifest and `manifest_digest` |
| `evidence-gate` | complete, independent-review gate and `review_digest` |

`safe-experiment-runner`, `ablation-analyzer`, and `research-memory` remain
discovery-only in this generic runtime. In particular, SafeRunner stays behind its
dedicated approval path. Calling a discovery-only package returns
`E_NOT_EXECUTABLE`, not a simulated success.

## Release lifecycle

The reference registry implements `draft`, deterministic `canary`, `active`, and
`retired` states, plus activate, retire, and rollback events. Canary routing hashes
the Skill name and correlation ID, so retrying one correlation makes the same routing
decision. Each invocation trace binds the correlation ID, version, package digest,
input digest, output digest, release state, and status.

Lifecycle behavior is covered by `tests/skills`, including canary stability,
activation, retirement, rollback, pin mismatch, and fail-closed execution. The
reference registry is currently in-process: state and traces reset with the API
process. There is no HTTP lifecycle mutation endpoint and no durable multi-replica
rollout coordinator yet.

## HTTP proof

With the local API running:

```bash
curl --fail http://127.0.0.1:8000/api/v1/skills
curl --fail http://127.0.0.1:8000/api/v1/skill-invocations/INVOCATION_ID
```

Invoke an allowlisted handler with:

```text
POST /api/v1/skills/{name}/invoke
{
  "correlation_id": "task-or-run-id",
  "expected_version": "0.1.0",
  "expected_package_digest": "64-lowercase-hex-characters",
  "payload": { ... typed handler input ... }
}
```

The API catalog reports all six packages and marks exactly three as executable.
Full copy-paste requests are in `docs/demo-runbook.md`.

Publication is a separate action: local packages are `draft` until a configured
registry confirms upload, review, and online status. No Nacos registry was configured
or queried for the 2026-08-29 verification snapshot.
