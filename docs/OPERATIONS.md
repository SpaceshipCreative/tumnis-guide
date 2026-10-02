# Operations

How to run a Tumnis install day to day: health, backups and the restore drill, upgrades and rollback, the master key, certificates and alerts. It assumes the install from the [README](../README.md): the repository checked out on the server, a `.env` in its root, and every command run from that root. Commands that change the stack are tested: the Upgrade and Rollback blocks are tagged `readme:upgrade`, and the README job (`.github/workflows/readme.yml`) runs them every night from the previous release to the current commit, once a release tag contains the README harness; until then that nightly job skips.

Where things live:

| What | Where |
| --- | --- |
| Deployment settings (passwords, host name, backups switch) | `.env` in the repository root, mode 600 ([.env.example](../.env.example) lists every variable) |
| Master key, API key pepper, `/metrics` token | `/etc/tumnis/secrets/tumnis_master_key`, `tumnis_pepper`, `tumnis_metrics_token`, owned by uid 10001 (the service user), mode 400 |
| HTTPS certificate and key | `/etc/tumnis/https/tumnis.crt` and `tumnis.key` |
| Backup repository keys | `/etc/pgbackrest/conf.d/secrets.conf` |
| Database, local backups, uploads | Docker volumes of the compose project `tumnis` (`pgdata`, `pgbackrest_repo1`, `spool`, ...) |
| Per-workspace secrets (OAuth clients, storage keys, Web Push keys) | Sealed in the database with the workspace's data key |

Lose the master key file and every sealed secret (OAuth clients, storage keys, Web Push keys, TOTP secrets) is unreadable: the secrets must be entered again and each person's TOTP reset (`tumnis admin reset-totp`). Lose the pepper file and every API key, runner token and session stops working: everyone signs in again and every key and runner token is issued again. Neither loss touches what only the other file protects. Keep a copy of both off the server (a password manager is fine), next to the backup cipher passphrase (ADR-0010).

## Health and monitoring

- `GET /health/live` does no I/O; its `Tumnis-Version` header names the running build.
- `GET /health/ready` answers 503 `down` when Postgres or DBOS is down or the database is behind this release's migrations, and 200 `degraded` when only a non-critical check fails (a module, an adapter's circuit breaker, or `backups`).
- `GET /metrics` serves Prometheus text behind `Authorization: Bearer <the token in tumnis_metrics_token>`. The scrape and blackbox configuration is in [deploy/README.md](../deploy/README.md), the alert rules in `deploy/prometheus/alerts.yml`.
- Logs are JSON on stdout: `docker compose logs -f api worker`.
- Settings > Metrics shows this install's own counts; Settings > Dead letters lists event deliveries that failed five times, to retry or discard.

| Alert | Fires when |
| --- | --- |
| `TumnisQueueBacklog` | a queue has had more than 100 waiting items for 10 minutes |
| `TumnisDeadLettersGrowing` | open dead letters grew in the last 30 minutes |
| `TumnisWalArchiveStale` | no WAL segment archived for over 5 minutes (critical) |
| `TumnisWalArchiveFailing` | a WAL archive attempt failed in the last 15 minutes (critical) |
| `TumnisBackupMissing` | no successful backup to a repository in 26 hours (critical) |
| `TumnisCertificateExpiring` | the HTTPS certificate expires within 14 days |
| `TumnisAuditChainBroken` | the audit log's hash chain failed verification (critical) |
| `TumnisAuditChainNotVerified` | the audit chain has not been verified in 26 hours |

The audit chain can be checked by hand at any time: `docker compose exec worker tumnis audit verify`.

## Backups

Postgres archives every WAL segment (at least once a minute) and pgBackRest keeps two repositories: repo1 on the `pgbackrest_repo1` volume (four weekly fulls, daily differentials, incrementals every six hours) and repo2 on Backblaze B2, encrypted on the client, written with a key that cannot delete. The schedules are in `deploy/pgbackrest/crontab` (UTC). Every run is recorded, and the worker's freshness check (every 15 minutes) marks `/health/ready` as `backups: degraded` when WAL or a backup is late. Recovery point objective 15 minutes, recovery time objective 1 hour (REL-1).

A new install starts with `TUMNIS_BACKUPS=off`: archiving and the backup service stay idle and readiness shows `backups: degraded` until this section is done.

1. In Backblaze B2, create a private bucket (versioning on, which is the default) with a lifecycle rule that deletes files 35 days after they are hidden or replaced. Name it `tumnis-backups`, or change `repo2-s3-bucket` in `deploy/pgbackrest/pgbackrest.conf`; set `repo2-s3-endpoint` and `repo2-s3-region` there to the bucket's region.
2. Create an application key limited to that bucket with `listBuckets, listFiles, readFiles, writeFiles` and without `deleteFiles`.
3. Write the keys and an encryption passphrase to `/etc/pgbackrest/conf.d/secrets.conf`, owned `root:999` (the image's postgres group), mode `0640`, and keep a copy of the passphrase offline: without it repo2 cannot be read.

   ```ini
   [global]
   repo2-s3-key=<B2 keyID>
   repo2-s3-key-secret=<B2 applicationKey>
   repo2-cipher-pass=<openssl rand -base64 48>
   ```

4. Turn backups on and restart the stack: Postgres restarts with archiving on, and the backup service creates the stanza in both repositories and takes a first full backup of each.

   ```bash
   sed -i 's/^TUMNIS_BACKUPS=.*/TUMNIS_BACKUPS=on/' .env
   docker compose up -d --wait
   docker compose logs -f backup        # stanza-create, check, then the first full backups
   ```

5. Check that the key cannot delete (T-P0-28-03): `scripts/drill/b2_no_delete_check.sh`.
6. Optionally bind the `pgbackrest_repo1` volume to a second disk.

What a backup holds: `docker compose exec backup pgbackrest --stanza=tumnis info`.

Project folders are backed up separately (REL-1): `scripts/backup/folders.sh REMOTE:PATH` copies every Tumnis-made folder, and every existing folder whose project opted in, to an rclone remote (`tumnis knowledge backup-sources` lists them). It only copies, so a file removed at the source stays in the backup. Run it nightly from the host's cron, with `tumnis` on the PATH through the worker container, for example a wrapper script that calls `docker compose exec -T worker tumnis "$@"`.

## Restore drill

A point-in-time restore from repo2 must meet the 15-minute RPO and the 1-hour RTO; the drill is the test, quarterly (REL-1). `scripts/drill/restore_drill.sh --mode prod` never stops or restores the production database: it reads from the production containers, restores repo2 into a new volume and a scratch Postgres on a drill network, runs the checks, and records the result in the production audit log (`tumnis drill record`; it exits 1 when the RPO or RTO was missed).

```bash
DRILL_SOURCE_PG=tumnis-postgres-1 DRILL_SOURCE_APP=tumnis-worker-1 \
  scripts/drill/restore_drill.sh --mode prod
```

The scheduled workflow `.github/workflows/restore-drill.yml` runs it on the self-hosted `homelab` runner on the 3rd of January, April, July and October (set the repository variables `DRILL_SOURCE_PG` and `DRILL_SOURCE_APP` and the secret `DRILL_SOURCE_DATABASE_URL` first). `--mode rehearsal` runs the same drill against the test stack with MinIO as repo2; the nightly workflow does that every night.

The drill only ever restores into its own scratch volume (`tumnis-drill-pgdata`) and a throwaway Postgres; it never restores or starts the production database. Restoring the production database is not scripted or rehearsed in this release. In outline, it is pgBackRest's own restore ([pgbackrest.org, Restore](https://pgbackrest.org/user-guide.html#restore)) into the `pgdata` volume, with every container of the stack stopped (`docker compose down`, never `down -v`): run the image's `pgbackrest --stanza=tumnis --repo=2 --delta restore` (repo2, the off-site copy the drill proves; `--repo=1` when the local `pgbackrest_repo1` volume survived) as the `postgres` user with that volume and the pgBackRest configuration mounted, adding `--type=time "--target=<UTC time>" --target-action=promote` for a point in time, then `docker compose up -d --wait`. Never restore over a running database. Take a fresh full backup afterwards, and rehearse the steps on a copy before you need them.

## Upgrade

Releases are tags `vX.Y.Z` with a [CHANGELOG.md](../CHANGELOG.md) section each; read it first, including its migration notes. Migrations are expand-only (squawk checks them in CI, and every release is rehearsed N, N+1, N), so the previous release keeps working on the new schema, which is what makes the rollback below safe. Back up first if backups are not on.

Set `TUMNIS_RELEASE` to the release you are moving to, for example `export TUMNIS_RELEASE=v1.1.0`. Keep the running image as `previous`, so a rollback needs no build:

```bash readme:upgrade:10
docker image tag ghcr.io/spaceshipcreative/tumnis:local ghcr.io/spaceshipcreative/tumnis:previous
```

Check out the release, build its image and restart. `migrate` runs first (the api and the workers wait for it), and `--wait` returns once the api answers `/health/ready`:

```bash readme:upgrade:20 timeout=2700
git fetch --tags origin
git checkout --detach "$TUMNIS_RELEASE"
docker build -f deploy/Dockerfile --build-arg VERSION="$TUMNIS_RELEASE" \
  -t ghcr.io/spaceshipcreative/tumnis:local .
docker compose up -d --build --wait --wait-timeout 1500
```

Compare `.env.example` with your `.env` after an upgrade: a new variable has a default, but its comment says when to change it.

## Rollback

Rollback is "redeploy the previous image" (REL-4). Never roll the database back: the previous release's `tumnis migrate` exits 0 with "database is ahead of this release (rollback)", and readiness treats a database ahead of the release as ready.

```bash readme:upgrade:30 timeout=900
docker image tag ghcr.io/spaceshipcreative/tumnis:previous ghcr.io/spaceshipcreative/tumnis:local
docker compose up -d --wait --wait-timeout 600
```

If the release also changed `deploy/`, check out the previous tag before that `docker compose up` so the compose file matches the image. The deploy host needs Docker Compose 2.24.0 or later: `deploy/compose.yaml` gives the worker `deploy/hosted-keys.env` through the long `env_file` form with `required: false`, which older Compose releases reject. Rolling back to a build older than the hosted provider settings (before commit 3f678e79): remove `deploy/hosted-keys.env` first, or that build's worker refuses the unknown variables and does not start. On any build that has them, a hosted key set with a base URL that is not `https://` stops the worker at startup (`hosted_url_requires_https`). DBOS resumes a workflow only on the application version that started it: with work in flight, keep one worker on the newer image running until its queues drain. On a Coolify install, point `TUMNIS_VERSION` back at the previous tag (or `sha-<commit>`) and deploy.

## Master key rotation

Per-workspace settings and secrets are sealed with each workspace's data key, which the master key wraps. The file is JSON, `{"active": 1, "keys": {"1": "<base64 of 32 random bytes>"}}`; the api and worker refuse to start (exit 78) when it is readable by group or others or, in prod, owned by anyone but root or the service user. To rotate without downtime:

1. Add a new version to the file and make it active (for example `"active": 2, "keys": {"1": "...", "2": "<openssl rand -base64 32>"}`), keeping the file's owner and mode.
2. Restart the api and the workers (`docker compose restart api worker worker-extract`): new data keys are wrapped under the new version, and both versions stay loaded.
3. Re-wrap every data key as the owner role; the sealed values do not change: `docker compose run --rm migrate tumnis keys rotate-master --to 2`.
4. Remove the old version from the file and restart again. Update the offline copy.

## Certificates

- **Tailscale certificate.** Let's Encrypt certificates last 90 days, and a certificate written to files is not renewed by Tailscale. Renew it monthly from root's crontab, then restart the proxy so Caddy loads it: `tailscale cert --cert-file /etc/tumnis/https/tumnis.crt --key-file /etc/tumnis/https/tumnis.key "$TUMNIS_HOST" && docker compose -f /path/to/tumnis-guide/deploy/compose.yaml --profile standalone restart proxy`. `TumnisCertificateExpiring` warns 14 days ahead when the blackbox probe is set up.
- **Self-signed certificate.** Valid 825 days; browsers and phones do not trust it. Use it to try Tumnis on the server itself, and switch to the Tailscale certificate for the phone.
- **Database TLS.** The database CA and the Postgres and PgBouncer certificates last `DB_TLS_DAYS` (3650 by default). To rotate, stop the stack, remove the `db_tls_ca`, `db_tls_postgres` and `db_tls_pgbouncer` volumes and start it again ([deploy/README.md](../deploy/README.md)).

## Account recovery and maintenance commands

Each runs in a container of the stack, which has the database URLs and the key files:

| Command | Does |
| --- | --- |
| `docker compose exec worker tumnis admin reset-totp you@example.com` | A new TOTP secret for a lost phone, printed once as an `otpauth://` URI; the old codes stop working |
| `docker compose exec worker tumnis audit verify` | Verifies every workspace's audit hash chain; exit 1 names each break |
| `docker compose run --rm migrate tumnis keys rotate-master --to <n>` | Re-wraps every data key under master key version `n` |
| `docker compose run --rm migrate tumnis migrate --check` | Exit 1 unless the database is at this release's migrations |
| `docker compose run --rm migrate tumnis embeddings index <model> --dims <n>` | Creates a new embedding model's index before switching models |
| `docker compose exec worker tumnis knowledge backup-sources` | The project folders the nightly folder backup copies |

## Releases

Versions follow [SemVer](https://semver.org), and every release has a dated section in [CHANGELOG.md](../CHANGELOG.md) ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/)). Each pull request with a user-visible change adds a line under `## [Unreleased]`. To release:

1. Work through [RELEASE-CHECKLIST.md](RELEASE-CHECKLIST.md) on a `release/X.Y.Z` branch; there the unit job requires every row to link its evidence.
2. Rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, add an empty `## [Unreleased]` above it, and merge.
3. Check the tag locally: `python3 scripts/release/check_changelog.py vX.Y.Z`.
4. Tag `main` and push the tag: `git tag -s vX.Y.Z -m "..." && git push origin vX.Y.Z`.
5. `.github/workflows/release.yml` rehearses the rollback from the previous tag on the homelab runner, then pushes `ghcr.io/spaceshipcreative/tumnis:vX.Y.Z` and creates the GitHub release, as a draft, with the changelog section as notes and three assets: the CycloneDX SBOM (`sbom.cdx.json`), the OpenAPI spec (`openapi-vX.Y.Z.json`) and the JSON Schemas (`schemas-vX.Y.Z.tar.gz`). It then reads the draft back and fails if any of the three is missing or empty, leaving the release a draft; only when all three are there does it publish the release. The README job also runs on the tag.

Rehearse any release locally with `scripts/release/rollback_rehearsal.sh <previous tag> HEAD` (Docker; the stack is `deploy/compose.test.yaml` as project `tumnis-skew` on 127.0.0.1:18080). Every pull request that changes a migration runs the same rehearsal from the latest `main` image (`.github/workflows/version-skew.yml`).
