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

Locally: `scripts/drill/restore_drill.sh --mode rehearsal` runs the whole drill against `compose.test.yaml` (repo2 on MinIO, throwaway keys in `pgbackrest/test-conf.d`, overrides in `pgbackrest/rehearsal.env`).

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
