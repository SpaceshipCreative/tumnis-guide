# ADR-0003: Generate the TypeScript client, Query hooks and zod schemas with openapi-ts
Status: Accepted (2026-09-27) | Supersedes: none

## Context
The React PWA talks to the FastAPI backend over `/v1` REST. The API is versioned and its OpenAPI spec is generated from code (SAAS-1), and architecture principle 8 says contracts are generated, not hand-kept. The frontend needs typed calls, TanStack Query hooks (ADR-0004) and zod schemas for route search params and React Hook Form, all matching the backend exactly.

## Options considered
- **@hey-api/openapi-ts.** Generates a fetch client, TanStack Query hooks and zod schemas from the OpenAPI spec in one step. Deal-breaker only if the spec is not kept in sync, which CI enforces.
- **Hand-written client and types.** No build step. Deal-breaker: it drifts from the backend silently, and every drift is a runtime bug.

## Decision
`make gen` exports the OpenAPI spec from the FastAPI app and runs `@hey-api/openapi-ts` to write the client, Query hooks and zod schemas into `frontend/src/api/`. That folder is generated and never edited by hand. OpenAPI operation IDs stay `<tag>_<function>` so generated names are stable.

## Consequences
- The OpenAPI spec is a build artifact; the Contract CI job regenerates it and the client and fails on any diff from what is committed (`make gen && git diff --exit-code`).
- Renaming a backend function or tag renames generated hooks, so it is a visible, reviewed change.
- The shared fetch wrapper (idempotency key, `version`, CSRF header) lives outside `src/api/` in `src/lib/fetch.ts`.

## Sources
- [Hey API configuration](https://heyapi.dev/openapi-ts/configuration)
- [Hey API fetch client](https://heyapi.dev/openapi-ts/clients/fetch)
- [Hey API TanStack Query plugin](https://heyapi.dev/openapi-ts/plugins/tanstack-query)
