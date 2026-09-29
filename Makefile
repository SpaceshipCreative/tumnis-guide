# Entry points. Bodies marked "not yet" are filled by the named work package.
.PHONY: check test test-int e2e gen seed up down guards

BACKEND := cd backend &&

## Lint, typecheck, boundaries and unit tests; must finish under 60 s (P0-01).
check:
	$(BACKEND) uv run ruff check .
	$(BACKEND) uv run ruff format --check .
	$(BACKEND) uv run mypy tumnis
	$(BACKEND) uv run lint-imports
	$(BACKEND) uv run pytest -q -n auto -m "not integration and not contract"
	@if [ -d frontend/node_modules ]; then \
		npm --prefix frontend run typecheck & typecheck=$$!; \
		npm --prefix frontend run lint && wait $$typecheck \
			&& npm --prefix frontend run test --if-present -- --run \
			&& node --test scripts/ci/check_bundle.test.mjs; \
	else \
		echo "skip frontend: frontend/node_modules absent (run npm ci in frontend/)"; \
	fi

## Unit and contract tests (sockets blocked).
test:
	$(BACKEND) uv run pytest -q -n auto -m "not integration"

## Integration tests: Postgres 18 + pgvector, DBOS, MinIO, SFTP, clamd (needs Docker).
test-int:
	$(BACKEND) uv run pytest -q -n auto -m integration

## Playwright journeys and acceptance, phone and laptop (the stack starts from compose.test
## once P0-04 lands; E2E_BASE_URL points at a running app instead).
e2e:
	cd frontend && npx playwright test

## Regenerate JSON Schemas, schemas/openapi.json, the schema contract tests and the
## openapi-ts client in frontend/src/api (P0-11); commit the result. CI fails on a diff.
gen:
	$(BACKEND) uv run tumnis gen all --out ..
	cd frontend && npx --no-install openapi-ts && npx --no-install prettier --write src/api

## Load the seed set into DATABASE_URL (SET=load for the 2,000-task set, ANCHOR=YYYY-MM-DD).
seed:
	$(BACKEND) uv run tumnis seed --set $(or $(SET),seed) $(if $(ANCHOR),--anchor $(ANCHOR))

## The compose.test stack (fakes, seed, api on 127.0.0.1:8080; TUMNIS_TEST_PORT moves it).
up:
	docker compose -f deploy/compose.test.yaml up -d --wait --build

down:
	docker compose -f deploy/compose.test.yaml down -v

## CI guards locally: spec-guard against origin/main, then the traceability report (P0-03).
guards:
	$(BACKEND) uv run python ../scripts/ci/spec_guard.py --base origin/main --head HEAD --repo .. --labels ""
	$(BACKEND) uv run python ../scripts/ci/traceability.py --out ../trace.md
