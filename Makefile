# Entry points. Bodies marked "not yet" are filled by the named work package.
.PHONY: check test test-int e2e gen seed up down guards

BACKEND := cd backend &&

## Lint, typecheck, boundaries and unit tests; must finish under 60 s (P0-01).
check:
	$(BACKEND) uv run ruff check .
	$(BACKEND) uv run ruff format --check .
	$(BACKEND) uv run mypy tumnis
	$(BACKEND) uv run lint-imports
	$(BACKEND) uv run pytest -q -m "not integration and not contract"
	@if [ -d frontend/node_modules ]; then \
		npm --prefix frontend run lint && npm --prefix frontend run test --if-present -- --run; \
	else \
		echo "skip frontend: frontend/node_modules absent (run npm ci in frontend/)"; \
	fi

## Unit and contract tests.
test:
	$(BACKEND) uv run pytest -q -m "not integration"

test-int:
	@echo "not yet (P0-02)"

e2e:
	@echo "not yet (P0-05)"

gen:
	@echo "not yet (P0-11)"

## Load the seed set into DATABASE_URL (SET=load for the 2,000-task set, ANCHOR=YYYY-MM-DD).
seed:
	$(BACKEND) uv run tumnis seed --set $(or $(SET),seed) $(if $(ANCHOR),--anchor $(ANCHOR))

up:
	@echo "not yet (P0-04)"

down:
	@echo "not yet (P0-04)"

guards:
	@echo "not yet (P0-03)"
