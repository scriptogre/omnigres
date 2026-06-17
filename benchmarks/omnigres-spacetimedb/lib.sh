#!/usr/bin/env bash
# Shared helpers for the benchmark scripts.
set -uo pipefail

# Strip the harmless omnigres "compiled against" warnings.
clean() { grep -vE "compiled against|^WARNING"; }

# Wait until Postgres in a container accepts connections.
wait_pg() { # $1=container $2=user
  until docker exec "$1" pg_isready -U "${2:-postgres}" >/dev/null 2>&1; do sleep 1; done
}

# In-DB parallel driver: spawn K backends INSIDE the container, each running
# CALL transfer_loop(M) (one CALL, then M transfers in-process). Low overhead,
# no per-transfer network round-trip. Prints aggregate transfers/sec.
#   indb_run <container> <db> <user> <K backends> <M per backend> <sync on|off>
indb_run() {
  local c=$1 db=$2 user=$3 K=$4 M=$5 sc=$6
  docker exec "$c" bash -c '
    db="'"$db"'"; user="'"$user"'"; K='"$K"'; M='"$M"'; sc="'"$sc"'"
    t0=$(date +%s.%N)
    for i in $(seq 1 $K); do
      PGOPTIONS="-c synchronous_commit=$sc" psql -U "$user" -d "$db" -q \
        -c "CALL transfer_loop($M)" >/dev/null 2>&1 &
    done
    wait
    t1=$(date +%s.%N)
    awk "BEGIN{printf \"%.0f transfers/s\n\", ($K*$M)/($t1-$t0)}"'
}

# Networked driver via pgbench (one TCP round-trip per transfer).
#   pgbench_run <container> <db> <user> <clients> <seconds> <script-in-container>
pgbench_run() {
  local c=$1 db=$2 user=$3 cl=$4 secs=$5 script=$6
  docker exec -e PGPASSWORD=postgres "$c" pgbench -U "$user" -h localhost -d "$db" \
    -c "$cl" -j "$cl" -T "$secs" --max-tries=10 -f "$script" 2>&1 | clean | grep -E "^tps ="
}
