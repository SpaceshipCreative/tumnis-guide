#!/bin/sh
# The server certificate comes from the deployment's database CA (the db-tls service,
# tumnis-db-tls.sh), mounted read-only at /etc/tumnis/tls (P0-16, SEC-9). A container
# started without it (the restore drill's scratch server) gets a throwaway self-signed
# certificate, so TLS stays on everywhere; clients of such a server can only use
# sslmode=require. Then hands over to the image's own entrypoint.
set -eu
TLS_DIR=/etc/tumnis/tls
if [ "$(id -u)" = 0 ] && [ ! -s "$TLS_DIR/postgres.crt" ]; then
  mkdir -p "$TLS_DIR"
  openssl req -new -x509 -days 3650 -nodes -newkey ec -pkeyopt ec_paramgen_curve:P-256 \
    -subj "/CN=postgres" -keyout "$TLS_DIR/postgres.key" -out "$TLS_DIR/postgres.crt" 2>/dev/null
  chown postgres:postgres "$TLS_DIR/postgres.crt" "$TLS_DIR/postgres.key"
  chmod 600 "$TLS_DIR/postgres.key"
fi
exec docker-entrypoint.sh "$@"
