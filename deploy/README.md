# deploy

Container and host configuration: the Dockerfile, the compose files (main, preview, test), and config for Postgres, pgBackRest, PgBouncer, clamd and Prometheus alerts.

## Backups (P0-28, REL-1)

Postgres archives WAL (`postgres/conf.d/archive.conf`, `archive_timeout = 60s`) through pgBackRest to two repositories (`pgbackrest/pgbackrest.conf`): repo1 on the `pgbackrest_repo1` volume, repo2 encrypted on Backblaze B2. The `backup` service creates the stanza, takes a first full backup per repository, then runs `pgbackrest/crontab` under supercronic; every run lands in `ops_backup_runs`, and the worker's `*/15` freshness check (production only) marks readiness `backups: degraded` when WAL or a backup is late. Previews never archive and never back up.

One-time setup on the homelab host:

1. B2: a private bucket (`tumnis-backups`, or change `repo2-s3-bucket`) with versioning (the default) and a lifecycle rule that deletes files 35 days after they are hidden or replaced. Set `repo2-s3-endpoint` and `repo2-s3-region` in `pgbackrest/pgbackrest.conf` to the bucket's region.
2. B2 application key restricted to that bucket with `listBuckets, listFiles, readFiles, writeFiles` and **without** `deleteFiles` (pgBackRest never expires repo2, so it never needs to delete there).
3. `/etc/pgbackrest/conf.d/secrets.conf`, owned `root:999` (the image's postgres group), mode `0640`:

   ```ini
   [global]
   repo2-s3-key=<B2 keyID>
   repo2-s3-key-secret=<B2 applicationKey>
   repo2-cipher-pass=<openssl rand -base64 48; keep a copy offline: without it repo2 cannot be read>
   ```

4. Optionally bind the `pgbackrest_repo1` volume to a second disk.
5. The drill runs on the self-hosted `homelab` runner (docker, jq): set the repository variables `DRILL_SOURCE_PG` and `DRILL_SOURCE_APP` to the production Postgres and api container names, and the secret `DRILL_SOURCE_DATABASE_URL` for the threshold re-check. Then run `scripts/drill/b2_no_delete_check.sh` and `gh workflow run restore-drill.yml -f mode=prod`.

Locally: `scripts/drill/restore_drill.sh --mode rehearsal` runs the whole drill against `compose.test.yaml` plus `compose.test-backups.yaml` (repo2 on MinIO, throwaway keys in `pgbackrest/test-conf.d`, overrides in `pgbackrest/rehearsal.env`). `compose.test.yaml` alone keeps WAL archiving off and the backup service idle, as previews do; add the overlay only where a test needs backups.

## Observability (P0-27, REL-5, FR-12.3)

Every api and worker log line is JSON on stdout with `trace_id` and `span_id` inside a request or a subscriber span; secrets, bodies and prompts are redacted before rendering. `GET /metrics` (root, not `/v1`) serves Prometheus text behind `Authorization: Bearer <token>`; the token is read from `METRICS_TOKEN_FILE` (`/etc/tumnis/secrets/tumnis_metrics_token` on the host, one line, `openssl rand -hex 32`), and the api refuses to start in prod without it. Alert rules live in `prometheus/alerts.yml` with unit tests in `prometheus/alerts.test.yml`:

```bash
docker run --rm -v "$PWD/deploy/prometheus:/rules:ro" -w /rules --entrypoint promtool prom/prometheus:v3.15.0 test rules alerts.test.yml
```

`SENTRY_DSN` (GlitchTip) and `OTEL_EXPORTER_OTLP_ENDPOINT` (an OTLP/HTTP collector) are optional; unset, the SDK stays off and spans go nowhere.

Homelab Prometheus scrape job (the token file is readable by Prometheus only):

```yaml
scrape_configs:
  - job_name: tumnis
    scheme: https
    metrics_path: /metrics
    authorization: { type: Bearer, credentials_file: /etc/prometheus/secrets/tumnis_metrics_token }
    static_configs: [{ targets: ["tumnis.lan"] }]
  - job_name: tumnis-tls          # blackbox exporter: feeds TumnisCertificateExpiring
    metrics_path: /probe
    params: { module: [http_2xx] }
    static_configs: [{ targets: ["https://tumnis.lan/health/live"] }]
    relabel_configs:
      - { source_labels: [__address__], target_label: __param_target }
      - { source_labels: [__param_target], target_label: instance }
      - { target_label: __address__, replacement: blackbox-exporter:9115 }
rule_files: [/etc/prometheus/rules/tumnis-alerts.yml]   # a copy of prometheus/alerts.yml
```

## TLS and the Security job (P0-16, SEC-9, SEC-7)

Every database hop is encrypted and verified. On first start the one-shot `db-tls` service (`postgres/tumnis-db-tls.sh`) creates a private database CA and issues a certificate for `postgres` and one for `pgbouncer` into the `db_tls_postgres` and `db_tls_pgbouncer` volumes, and the CA certificate into `db_tls_ca`; the CA key is never stored. Postgres accepts only TLS 1.2+ (`pg_hba.conf`: `hostssl` for the two app roles, `hostnossl ... reject`); PgBouncer requires TLS from clients and verifies Postgres (`server_tls_sslmode = verify-full`); the api, worker and migrate connect with `sslmode=verify-full&sslrootcert=/etc/tumnis/tls/ca.crt`, and in prod refuse to start on anything weaker (`database_tls_required`). Extra certificate names: `DB_TLS_POSTGRES_NAMES`, `DB_TLS_PGBOUNCER_NAMES` (comma-separated) on the `db-tls` service. To rotate (the certificates last `DB_TLS_DAYS`, 3650 by default), stop the stack, remove the three `db_tls_*` volumes and start it again. A Postgres container started without the volume (the drill's scratch server) makes a throwaway self-signed certificate, so TLS stays on (clients use `sslmode=require` there).

The CI Security job blocks a merge on any high or critical finding: pip-audit, npm audit, gitleaks (`.gitleaks.toml`), Semgrep (registry packs plus `.semgrep/tumnis.yml`) and Trivy on the built image. A finding with no fix yet goes in `.trivyignore` with an issue link and `exp:YYYY-MM-DD` at most 30 days out. `scripts/ci/sbom.sh <image> sbom.cdx.json` writes the CycloneDX SBOM the release attaches (P0-30).

## Hosted provider keys (Scott decisions 75 and 90)

The optional hosted speech (`SPEECH__HOSTED_*`, P4-03) and hosted embedder (`EMBEDDINGS__HOSTED_*`, P3-10) settings go in `deploy/hosted-keys.env` beside `compose.yaml`, not in `.env`: copy `hosted-keys.env.example`, uncomment only the lines you use, and keep it mode 600 (it is gitignored). Only the worker reads it (`env_file` with `required: false`), so without the file no service gets any of these variables, not even empty ones. That keeps rollback working (REL-4): an image older than these settings refuses unknown variables, so before rolling back past commit 3f678e79 (the merge that added them), remove the file. Values are interpolated as in `.env` (write a `$` as `$$`), each provider is off until its URL, model and key are all set, a URL must be `https://` when its key is set (the worker refuses to start otherwise, `hosted_url_requires_https`), a preview refuses a key, and the keys never go into the database. `env_file`'s `required` needs Docker Compose 2.20.0 or later, the same floor as the `include` in `compose.test.yaml`.
