# syntax=docker/dockerfile:1
# Postgres 18 with pgvector (ADR-0006), pgBackRest and supercronic (P0-28). The same image
# runs the database, the backup scheduler (compose service `backup`) and the drill's
# scratch restore, so a restore always uses the release's Postgres major and extensions.
# Build through compose: the `pgbackrest` build context is deploy/pgbackrest.
# TLS (P0-16): tumnis-db-tls.sh issues the database certificates (compose service db-tls).

FROM pgvector/pgvector:pg18@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a AS base

# supercronic (cron for containers, UTC, logs to stdout), pinned by release and sha256.
FROM scratch AS supercronic-amd64
ADD --checksum=sha256:a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1 \
    https://github.com/aptible/supercronic/releases/download/v0.2.49/supercronic-linux-amd64 /supercronic
FROM scratch AS supercronic-arm64
ADD --checksum=sha256:02aa0cb229ba09050cba6638059dadb9eedc2276632ea43d6a57a2f8c1629dd5 \
    https://github.com/aptible/supercronic/releases/download/v0.2.49/supercronic-linux-arm64 /supercronic
FROM supercronic-${TARGETARCH} AS supercronic

FROM base

RUN apt-get update \
    && apt-get install -y --no-install-recommends pgbackrest=2.59.1-1.pgdg12+1 \
    && rm -rf /var/lib/apt/lists/* /etc/pgbackrest.conf \
    && install -d -o postgres -g postgres -m 0750 \
        /etc/pgbackrest /etc/pgbackrest/conf.d /var/lib/pgbackrest /var/spool/pgbackrest

COPY --from=supercronic --chmod=0755 /supercronic /usr/local/bin/supercronic
COPY postgresql.conf pg_hba.conf /etc/postgresql/
COPY conf.d/ /etc/postgresql/conf.d/
COPY tumnis-entrypoint.sh tumnis-db-tls.sh /usr/local/bin/
COPY --from=pgbackrest --chmod=0644 pgbackrest.conf crontab /etc/pgbackrest/
COPY --from=pgbackrest --chmod=0755 run-backup.sh init-stanza.sh /usr/local/bin/

ENTRYPOINT ["tumnis-entrypoint.sh"]
CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
