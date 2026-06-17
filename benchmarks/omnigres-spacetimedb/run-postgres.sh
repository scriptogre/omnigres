#!/usr/bin/env bash
# In-memory Postgres benchmark with separated client/server CPU.
#
# pgbench runs in a SEPARATE container from Postgres, connected via Docker
# network. This matches SpacetimeDB's setup (Node.js client on host,
# SpacetimeDB in Docker).
#
# Config: UNLOGGED tables, sync_commit=off, fsync=off, shared_buffers=2GB.
# Both systems fully in-memory, no disk I/O.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/lib.sh"
IMAGE="${1:-ghcr.io/omnigres/omnigres-17:latest}"
SRV=pg_server; U=omnigres; DB=omnigres
[ "$IMAGE" = "ghcr.io/omnigres/omnigres-17:latest" ] || { U=postgres; DB=postgres; }
NET=bench_net
DURATION=60
RUNS=3

docker rm -f "$SRV" >/dev/null 2>&1
docker network create "$NET" 2>/dev/null || true

echo "## starting Postgres server (separate container, own CPU budget)"
docker run -d --name "$SRV" --network "$NET" --shm-size=2g \
  -e POSTGRES_PASSWORD=postgres "$IMAGE" >/dev/null
wait_pg "$SRV" "$U"

echo "## applying in-memory config"
docker cp "$HERE/sql/optimized.conf" "$SRV":/tmp/optimized.conf >/dev/null
docker exec "$SRV" bash -c '
  cfg="$PGDATA/postgresql.conf" 2>/dev/null || cfg="/var/lib/postgresql/data/postgresql.conf"
  cat /tmp/optimized.conf >> "$cfg"
  echo "max_connections = 300" >> "$cfg"
'
docker restart "$SRV" >/dev/null; wait_pg "$SRV" "$U"

echo "## loading schema (UNLOGGED, fully in-memory)"
docker cp "$HERE/sql/setup.sql" "$SRV":/tmp/setup.sql >/dev/null
docker exec "$SRV" psql -U "$U" -d "$DB" -v persistence=UNLOGGED \
  -f /tmp/setup.sql >/dev/null 2>&1
NPROC=$(docker exec "$SRV" nproc)
echo "server cores: $NPROC"

# pgbench from a separate container (own CPU, connects over Docker network)
pgbench_ext() { # $1=clients $2=seconds $3=script
  docker run --rm --network "$NET" \
    -v "$HERE/sql:/scripts:ro" \
    -e PGPASSWORD=postgres \
    postgres:17 \
    pgbench -h "$SRV" -U "$U" -d "$DB" \
    -c "$1" -j "$1" -T "$2" -M prepared --max-tries=10 \
    -f "/scripts/$3" 2>&1 | grep -vE "compiled against|^WARNING" | grep -E "^tps ="
}

echo
echo "============================================================"
echo "  pgbench stored procedure, SEPARATE client container"
echo "  Server: $SRV ($NPROC cores) | Client: separate postgres:17"
echo "  ${DURATION}s per run, $RUNS runs at peak"
echo "============================================================"

# pull client image once
docker pull postgres:17 >/dev/null 2>&1

echo
echo "--- sweep client count, alpha=0 ---"
for CL in 4 8 12 16 24 32 48 64; do
  echo -n "  $CL clients : "
  pgbench_ext "$CL" "$DURATION" transfer_a0.sql
done

echo
echo "--- sweep client count, alpha=1.5 ---"
for CL in 4 8 12 16 24 32 48 64; do
  echo -n "  $CL clients : "
  pgbench_ext "$CL" "$DURATION" transfer_a15.sql
done

echo
echo "--- $RUNS repeated runs at best client counts (60s each) ---"
echo "alpha=0, 12 clients:"
for R in $(seq 1 $RUNS); do
  echo -n "  run $R : "
  pgbench_ext 12 "$DURATION" transfer_a0.sql
done
echo "alpha=1.5, 8 clients:"
for R in $(seq 1 $RUNS); do
  echo -n "  run $R : "
  pgbench_ext 8 "$DURATION" transfer_a15.sql
done

echo
echo "============================================================"
echo "  Done. Compare against SpacetimeDB (same machine)."
echo "============================================================"

docker rm -f "$SRV" >/dev/null 2>&1
docker network rm "$NET" >/dev/null 2>&1
