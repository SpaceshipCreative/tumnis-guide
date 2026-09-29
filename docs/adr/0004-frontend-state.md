# ADR-0004: TanStack Router for URL state, TanStack Query for server data, XState for device state; no Zustand
Status: Accepted (2026-09-27) | Supersedes: none

## Context
The PWA must deep-link from Discord messages and push notifications into an exact item, keep server data fresh from a `/ws` socket, update optimistically with rollback on 409 (REL-2), run the focus, voice and offline-queue behaviors reliably on a phone (FR-10, FR-3.10), and keep offline quick-adds in IndexedDB. Every piece of state needs one owner, so nothing is stored twice.

## Options considered
- **TanStack Router + TanStack Query + XState (and XState Store).** Typed, zod-validated search params for URL state; a query cache with invalidation, prefetching and optimistic updates for server data; explicit, testable state machines for device behavior. Deal-breaker only if owners blur.
- **Zustand for everything.** One small library. Deal-breaker: caching, invalidation, refetch and optimistic rollback for server data are rebuilt by hand, and complex device flows become ad-hoc flags.

## Decision
State lives in four places, each with one owner: the URL (TanStack Router, search params typed with zod), the TanStack Query cache (server data, hooks generated per ADR-0003), XState (device-only state: the UI store in XState Store, and the `focusSession`, `voiceMode` and `offlineQueue` machines) and IndexedDB (what must survive being offline). Forms use React Hook Form with the generated zod schemas. Zustand is not used.

## Consequences
- Two libraries for state, each with a clear owner; nothing the server owns is copied into a store.
- Machines are tested by sending events and asserting states, with no rendering; every guard and action a machine references is declared in `setup({ guards, actions })`.
- Queries use a long `staleTime`, because the socket, not a timer, says when data changed.
- Route loaders prefetch their queries so screens render with data on first paint.

## Sources
- [TanStack Router search params guide](https://tanstack.com/router/latest/docs/framework/react/guide/search-params)
- [TanStack Query prefetching](https://tanstack.com/query/latest/docs/framework/react/guides/prefetching)
- [TanStack Query optimistic updates](https://tanstack.com/query/latest/docs/framework/react/guides/optimistic-updates)
- [XState setup](https://stately.ai/docs/setup)
- [XState Store](https://stately.ai/docs/xstate-store)
