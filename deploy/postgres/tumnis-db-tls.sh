#!/bin/sh
# The deployment's database CA and the certificates it issues (P0-16, SEC-9). Runs once as
# the db-tls service (root) before Postgres and PgBouncer start; later runs find the
# certificates and exit.
#
#   /tls/ca/ca.crt                        -> api, worker, migrate (sslrootcert)
#   /tls/postgres/postgres.{crt,key}      -> Postgres (owner 999, the image's postgres user)
#   /tls/pgbouncer/pgbouncer.{crt,key}    -> PgBouncer (owner 70), with ca.crt beside them
#
# Each server certificate names its compose service (DNS:postgres, DNS:pgbouncer; more
# names through DB_TLS_POSTGRES_NAMES and DB_TLS_PGBOUNCER_NAMES), so clients connect with
# sslmode=verify-full. The CA key never touches a volume: it exists only while this script
# runs. To rotate, remove the three db_tls_* volumes and restart the stack.
set -eu
CA_OUT=/tls/ca
PG_OUT=/tls/postgres
BOUNCER_OUT=/tls/pgbouncer
DAYS="${DB_TLS_DAYS:-3650}"
PG_UID=999
BOUNCER_UID=70

if [ -s "$CA_OUT/ca.crt" ] && [ -s "$PG_OUT/postgres.crt" ] && [ -s "$BOUNCER_OUT/pgbouncer.crt" ]; then
  echo "db-tls: certificates present, nothing to do"
  exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
umask 077

openssl req -x509 -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:P-256 -days "$DAYS" \
  -subj "/CN=Tumnis database CA" -keyout "$WORK/ca.key" -out "$WORK/ca.crt" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null

# issue <name> <extra DNS names, comma-separated> <out dir> <owner uid>
issue() {
  san="DNS:$1"
  for extra in $(echo "$2" | tr ',' ' '); do san="$san,DNS:$extra"; done
  openssl req -new -nodes -newkey ec -pkeyopt ec_paramgen_curve:P-256 -subj "/CN=$1" \
    -keyout "$WORK/$1.key" -out "$WORK/$1.csr" 2>/dev/null
  printf 'subjectAltName=%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n' \
    "$san" > "$WORK/$1.ext"
  openssl x509 -req -in "$WORK/$1.csr" -CA "$WORK/ca.crt" -CAkey "$WORK/ca.key" \
    -set_serial "0x$(openssl rand -hex 16)" -days "$DAYS" -sha256 \
    -extfile "$WORK/$1.ext" -out "$WORK/$1.crt" 2>/dev/null
  install -d -m 0755 "$3"
  install -m 0644 "$WORK/ca.crt" "$3/ca.crt"
  install -o "$4" -g "$4" -m 0644 "$WORK/$1.crt" "$3/$1.crt"
  install -o "$4" -g "$4" -m 0600 "$WORK/$1.key" "$3/$1.key"
}

issue postgres "${DB_TLS_POSTGRES_NAMES:-}" "$PG_OUT" "$PG_UID"
issue pgbouncer "${DB_TLS_PGBOUNCER_NAMES:-}" "$BOUNCER_OUT" "$BOUNCER_UID"
install -d -m 0755 "$CA_OUT"
install -m 0644 "$WORK/ca.crt" "$CA_OUT/ca.crt"
echo "db-tls: issued postgres and pgbouncer certificates (valid $DAYS days)"
