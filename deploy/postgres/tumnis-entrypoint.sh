#!/bin/sh
# Makes a self-signed server certificate on first start (sslmode=require encrypts, it does
# not verify; P0-16 mounts a CA-issued certificate for verify-full), then hands over to the
# image's own entrypoint. The certificate lives on the data volume, beside PGDATA.
set -eu
TLS_DIR=/var/lib/postgresql/tls
if [ "$(id -u)" = 0 ] && [ ! -s "$TLS_DIR/server.crt" ]; then
  mkdir -p "$TLS_DIR"
  openssl req -new -x509 -days 3650 -nodes -subj "/CN=postgres" \
    -keyout "$TLS_DIR/server.key" -out "$TLS_DIR/server.crt" 2>/dev/null
  chown -R postgres:postgres "$TLS_DIR"
  chmod 600 "$TLS_DIR/server.key"
fi
exec docker-entrypoint.sh "$@"
