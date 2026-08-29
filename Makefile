PYTHON ?= python3
UV ?= uv
API_URL ?= http://127.0.0.1:8000

.PHONY: install install-api install-mcp install-web test test-api test-rxp test-skills test-proof test-postgres test-acceptance check-api test-benchmark benchmark benchmark-release demo-proof test-agentteams check-agentteams test-experiments test-web test-mcp verify openapi up down logs package

install: install-api install-mcp install-web

install-api:
	$(UV) sync --python 3.9 --extra dev

install-mcp:
	$(UV) sync --python 3.12 --project mcp_servers --extra dev

install-web:
	npm --prefix apps/web ci

test: test-api test-rxp test-skills test-proof check-api test-benchmark test-acceptance test-agentteams check-agentteams test-experiments test-mcp test-web verify

test-api:
	$(UV) run --python 3.9 --extra dev pytest tests/api

test-rxp:
	$(UV) run --python 3.9 --extra dev pytest tests/protocols
	$(UV) run --python 3.9 --extra dev python -m protocols.rxp schema --check

test-skills:
	$(UV) run --python 3.9 --extra dev pytest tests/skills

test-proof:
	$(UV) run --python 3.9 --extra dev pytest tests/proofs
	$(UV) run --python 3.9 --extra dev ruff check scripts/build_semifinal_proof.py scripts/verify_submission.py tests/proofs

test-postgres:
	test -n "$(EGO_TEST_POSTGRES_URL)"
	EGO_TEST_POSTGRES_URL="$(EGO_TEST_POSTGRES_URL)" $(UV) run --python 3.9 --extra dev pytest tests/postgres

check-api:
	$(UV) run --python 3.9 --extra dev ruff check apps/api protocols/rxp skill_runtime tests/api tests/protocols tests/skills
	$(UV) run --python 3.9 --extra dev mypy apps/api protocols/rxp skill_runtime

test-benchmark:
	$(UV) run --python 3.9 --extra dev pytest tests/benchmarks
	$(UV) run --python 3.9 --extra dev ruff check benchmarks tests/benchmarks
	$(UV) run --python 3.9 --extra dev mypy benchmarks
	$(UV) run --python 3.9 --extra dev python -m benchmarks.runner --repetitions 2 --strict --output-json /tmp/rxp-bench-ci.json --output-md /tmp/rxp-bench-ci.md

test-acceptance:
	$(UV) run --python 3.9 --extra dev pytest tests/acceptance
	$(UV) run --python 3.9 --extra dev ruff check semifinal_acceptance tests/acceptance
	$(UV) run --python 3.9 --extra dev mypy semifinal_acceptance

benchmark:
	$(UV) run --python 3.9 --extra dev python -m benchmarks.runner --strict

benchmark-release:
	@test -n "$(EVIDENCE_DIR)" || (echo "EVIDENCE_DIR must name a new or empty persistent directory" >&2; exit 2)
	$(UV) run --python 3.9 --extra dev python -m benchmarks.runner --profiles agentteams-rxp-target --release-gate agentteams-rxp-target --evidence-dir "$(EVIDENCE_DIR)"

demo-proof:
	$(UV) run --python 3.9 --extra dev python scripts/build_semifinal_proof.py
	$(UV) run --python 3.9 --extra dev python scripts/build_semifinal_proof.py --check

test-agentteams:
	$(UV) run --python 3.9 --extra dev pytest tests/agentteams

check-agentteams:
	$(UV) run --python 3.9 --extra dev ruff check apps/agentteams_bridge integrations/agentteams/benchmark_adapter.py tests/agentteams
	$(UV) run --python 3.9 --extra dev mypy apps/agentteams_bridge integrations/agentteams/benchmark_adapter.py
	$(PYTHON) integrations/agentteams/scripts/verify_official_contract.py --offline

test-experiments:
	$(UV) run --python 3.9 --extra dev pytest tests/experiments
	$(UV) run --python 3.9 --extra dev ruff check experiments tests/experiments
	$(UV) run --python 3.9 --extra dev mypy experiments/fashion_mnist_amp/contract.py experiments/fashion_mnist_amp/verify.py

test-web:
	npm --prefix apps/web test
	npm --prefix apps/web run build

test-mcp:
	$(UV) run --python 3.12 --project mcp_servers --extra dev pytest mcp_servers/tests tests/integration
	$(UV) run --python 3.12 --project mcp_servers --extra dev ruff check mcp_servers/src mcp_servers/tests tests/integration

verify:
	$(PYTHON) scripts/verify_submission.py

openapi:
	$(UV) run --python 3.9 --extra dev python scripts/export_openapi.py

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

package: openapi
	$(PYTHON) scripts/build_submission.py
