# Postgres 18 with pgvector (ADR-0006) and pgBackRest. P0-28 adds the pgBackRest stanza,
# repositories and schedules; P0-16 swaps the self-signed certificate for a CA-issued one.
FROM pgvector/pgvector:pg18@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a

RUN apt-get update \
    && apt-get install -y --no-install-recommends pgbackrest \
    && rm -rf /var/lib/apt/lists/*

COPY postgresql.conf pg_hba.conf /etc/postgresql/
COPY tumnis-entrypoint.sh /usr/local/bin/tumnis-entrypoint.sh

ENTRYPOINT ["tumnis-entrypoint.sh"]
CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
