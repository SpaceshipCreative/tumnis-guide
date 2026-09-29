# ADR-0011: Outbox relay enqueues DBOS workflows with event_id:subscriber deduplication
Status: Proposed (2026-09-27) | Supersedes: none

## Context
Modules react to each other through events (ADR-0001), and PRD decision 10 puts the event outbox in Postgres. A module's state change and the event it emits must commit together or not at all. Each subscriber must run independently, retry with backoff and land in the dead-letter view when it keeps failing, without touching other subscribers (REL-3). Subscribers run as DBOS workflows (ADR-0002).

## Options considered
- **Transactional outbox plus a relay that enqueues one DBOS workflow per subscriber, deduplicated on `event_id:subscriber`.** The event row commits with the state change; a crash between enqueue and marking the row sent only re-enqueues a duplicate that DBOS drops. Deal-breaker only if a subscriber is not idempotent.
- **Enqueue DBOS workflows directly inside the business transaction.** One less hop. Deal-breaker: not guaranteed atomic with the business transaction.

## Decision
A module changes its rows and inserts an `outbox` row in the same transaction through `tumnis/core/outbox.py` `emit()`, then fires `NOTIFY outbox`. The worker's relay wakes on the notify (and polls every few seconds as a backstop), claims rows with `FOR UPDATE SKIP LOCKED`, and for each registered subscriber enqueues a DBOS workflow whose workflow ID and deduplication ID are both `event_id:subscriber`, then marks the row sent. `SetWorkflowID` makes the enqueue idempotent for good (DBOS 3.1.0 returns the existing workflow for a known ID, even after it finished), while the deduplication ID alone would only block a duplicate while the first is queued or running; the pair gives exactly-once enqueue across relay crashes (verified against the pinned DBOS 3.1.0). Each subscriber runs in its own workflow. The trace context is stored on the outbox row and carried into the workflow.

## Consequences
- Events are delivered at least once; every subscriber must be idempotent.
- One failing subscriber retries and then dead-letters on its own; others are unaffected.
- The relay needs a direct (non-PgBouncer) connection for `LISTEN`.
- A broker can be put behind the outbox later without changing any module.
- Tests: relay integration tests prove one enqueue per subscriber, dedup across a crash, and dead-lettering (`backend/tumnis/core/tests/integration/test_relay.py`, `test_dead_letters.py`, `test_outbox.py`; the kill-and-resume pair also runs 20 times nightly).
- Status stays Proposed until Scott accepts it.

## Sources
- [DBOS queues tutorial](https://docs.dbos.dev/python/tutorials/queue-tutorial) (deduplication)
- [PostgreSQL NOTIFY](https://www.postgresql.org/docs/18/sql-notify.html)
- [PostgreSQL SELECT locking clause (SKIP LOCKED)](https://www.postgresql.org/docs/18/sql-select.html#SQL-FOR-UPDATE-SHARE)
- [Transactional outbox pattern](https://microservices.io/patterns/data/transactional-outbox.html)
