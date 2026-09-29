# ADR-0006: pgBackRest with Postgres run in the compose file
Status: Accepted (2026-09-27) | Supersedes: none

## Context
REL-1 requires a 15-minute recovery point and a 1-hour recovery time. The PRD placed Postgres as a Coolify-managed database with Coolify's scheduled backups, but Coolify backs up PostgreSQL with `pg_dump`, which is a periodic logical dump and cannot meet a 15-minute recovery point. All data, DBOS state and the outbox live in this one cluster (ADR-0001, ADR-0002), so its backup is the backup of the whole system.

## Options considered
- **Postgres in the compose file with pgBackRest and WAL archiving.** Point-in-time recovery, local and Backblaze B2 repositories, encryption, full/differential/incremental schedules. Deal-breaker only in that upgrades and tuning become ours.
- **Coolify-managed Postgres.** Coolify handles it. Deal-breaker: its `pg_dump` backups cannot meet the 15-minute recovery point.

## Decision
Postgres 18 with pgvector and pgBackRest runs as the `postgres` container in the compose file that Coolify deploys. WAL is archived through pgBackRest with `archive_timeout = 60s`. Repository 1 is local disk on a separate volume (full weekly, differential daily, incremental every 6 hours, expired by pgBackRest). Repository 2 is Backblaze B2 through its S3 API with pgBackRest encryption and a key that can write but not delete; a B2 lifecycle rule removes files older than 35 days. Coolify's dump backups may stay on as a second, independent copy.

## Consequences
- Postgres upgrades, tuning and extension management are ours, not Coolify's.
- A quarterly restore drill restores repository 2 to a scratch container, runs the migration check and smoke tests, and records the measured recovery time; a failed drill fails its job.
- The first drill must confirm pgBackRest runs cleanly with a no-delete key on B2; the fallback is B2 Object Lock with a key that has delete rights.
- The PRD's Deployment and Data ownership rows need the matching edit (architecture open question 5).

## Sources
- [pgBackRest user guide](https://pgbackrest.org/user-guide.html)
- [Coolify database backups](https://coolify.io/docs/databases/backups)
- [PostgreSQL continuous archiving and PITR](https://www.postgresql.org/docs/18/continuous-archiving.html)
