# ADR-0002: DBOS Transact for workflows, queues and schedules
Status: Accepted (2026-09-27) | Supersedes: none

## Context
Anything in Tumnis that takes more than one step or waits (an agent run, an approval with no deadline, a connector sync, a document extraction, the morning plan) must survive a restart or a deploy and resume from the last finished step instead of starting over (architecture principle 4, REL-3). Runs need per-project concurrency limits (SAF-5), syncs need per-provider rate limits, and triage must stay under Jev's 1,200 decisions per minute. PRD decision 10 rules out a separate broker: state lives in Postgres.

## Options considered
- **DBOS Transact.** Durable workflows and steps checkpointed in Postgres, queues with concurrency, partitions, rate limits and deduplication, scheduled workflows, and `send`/`recv`/`set_event`/`get_event` between workflows. State sits in the same cluster, so one backup covers it. Deal-breaker only if its determinism and versioning rules are ignored.
- **Procrastinate.** Postgres-backed and simple. Deal-breaker: single-step jobs only, so multi-step durable workflows and long human waits would be rebuilt by hand.
- **Temporal.** Mature durable execution. Deal-breaker: a separate cluster to run, back up and upgrade for one operator.

## Decision
The `worker` process launches DBOS and runs every workflow, queue and schedule; `worker-extract` listens only to the `extract` queue. DBOS system tables live in the Tumnis Postgres cluster. Workflows are in each module's `workflows.py`; `DBOS.recv` is called only in a workflow body, never inside a step or an HTTP handler; schedules register with `DBOS.apply_schedules` in `worker.py`. The `runs` queue is partitioned by project with 2 concurrent runs per project.

## Consequences
- Workflow code must be deterministic between steps: time, randomness and I/O happen inside steps.
- DBOS recovers a workflow only on a process running the same application version, so a rollout keeps one worker on the previous version until its in-flight workflows finish, and long human waits store only IDs and plain data.
- Every DBOS workflow has a kill-and-resume test in the integration suite (TDD rule 8), using the `worker_killer` fixture.
- Every pgBackRest backup includes in-flight workflows.
- DBOS upgrades are manual-merge in Renovate (label `needs-suite`) and pass the workflow suite first.

## Sources
- [DBOS Python programming guide](https://docs.dbos.dev/python/programming-guide)
- [DBOS queues tutorial](https://docs.dbos.dev/python/tutorials/queue-tutorial)
- [DBOS workflow communication](https://docs.dbos.dev/python/tutorials/workflow-communication)
- [DBOS scheduled workflows](https://docs.dbos.dev/python/tutorials/scheduled-workflows)
- [DBOS workflow recovery and application versions](https://docs.dbos.dev/production/self-hosting/workflow-recovery)
