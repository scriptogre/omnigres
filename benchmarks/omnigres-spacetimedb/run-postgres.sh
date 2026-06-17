#!/usr/bin/env bash
# Postgres / omnigres transfer benchmark: networked vs in-DB, across durability tiers.
# Usage: ./run-postgres.sh [image]   (default: ghcr.io/omnigres/omnigres-17:latest)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/lib.sh"
IMAGE="${1:-ghcr.io/omnigres/omnigres-17:latest}"
C=pgbench_omni; U=omnigres; DB=omnigres
[ "$IMAGE" = "ghcr.io/omnigres/omnigres-17:latest" ] || { U=postgres; DB=postgres; }

docker rm -f "$C" >/dev/null 2>&1
docker run -d --name "$C" --shm-size=2g -e POSTGRES_PASSWORD=postgres \
  -p 127.0.0.1:5432:5432 "$IMAGE" >/dev/null
wait_pg "$C" "$U"

echo "## applying optimized config"
docker cp "$HERE/sql/optimized.conf" "$C":/tmp/optimized.conf >/dev/null
docker exec "$C" bash -c 'cat /tmp/optimized.conf >> "$PGDATA/postgresql.conf" 2>/dev/null \
  || cat /tmp/optimized.conf >> /var/lib/postgresql/data/postgresql.conf'
docker restart "$C" >/dev/null; wait_pg "$C" "$U"

echo "## loading schema + transfer functions (heap, UNLOGGED for max throughput)"
docker cp "$HERE/sql/setup.sql" "$C":/tmp/setup.sql >/dev/null
docker cp "$HERE/sql/transfer_a0.sql"  "$C":/tmp/ >/dev/null
docker cp "$HERE/sql/transfer_a15.sql" "$C":/tmp/ >/dev/null
docker exec "$C" psql -U "$U" -d "$DB" -v persistence=UNLOGGED -f /tmp/setup.sql >/dev/null 2>&1
NPROC=$(docker exec "$C" nproc)
echo "cores in container: $NPROC"

echo; echo "===== NETWORKED (pgbench, one round-trip per transfer), $((NPROC*2)) clients ====="
echo -n "alpha=0   : "; pgbench_run "$C" "$DB" "$U" $((NPROC*2)) 30 /tmp/transfer_a0.sql
echo -n "alpha=1.5 : "; pgbench_run "$C" "$DB" "$U" $((NPROC*2)) 30 /tmp/transfer_a15.sql

echo; echo "===== IN-DB (no round-trips), alpha=0, durability tiers ====="
echo "--- no durability (UNLOGGED table, synchronous_commit=off) ---"
echo -n "${NPROC} backends : "; indb_run "$C" "$DB" "$U" "$NPROC"      120000 off
echo -n "$((NPROC*2)) backends : "; indb_run "$C" "$DB" "$U" $((NPROC*2)) 80000 off

echo "--- full durability (logged table, synchronous_commit=on + group commit) ---"
docker exec "$C" psql -U "$U" -d "$DB" -v persistence='' -f /tmp/setup.sql >/dev/null 2>&1
for K in "$NPROC" $((NPROC*8)) $((NPROC*16)); do
  echo -n "$K backends : "; indb_run "$C" "$DB" "$U" "$K" $((1500000/K)) on
done
echo
echo "(For RAM-backed durable: recreate with --mount type=tmpfs,destination=<PGDATA>,tmpfs-size=4g)"
echo "(Measure raw disk fsync rate: docker exec $C pg_test_fsync -s 5 -f \$PGDATA/ft)"
