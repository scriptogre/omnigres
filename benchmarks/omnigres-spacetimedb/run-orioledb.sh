#!/usr/bin/env bash
# OrioleDB vs heap: transfer throughput + WAL-volume-per-transaction.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; source "$HERE/lib.sh"
C=oriole
docker rm -f "$C" >/dev/null 2>&1
docker run -d --name "$C" --shm-size=2g -e POSTGRES_PASSWORD=postgres \
  -p 127.0.0.1:5433:5432 orioledb/orioledb:latest-pg17 >/dev/null
wait_pg "$C" postgres
docker exec "$C" psql -U postgres -tAc "alter system set synchronous_commit=off;" >/dev/null 2>&1
docker exec "$C" psql -U postgres -tAc "select pg_reload_conf();" >/dev/null 2>&1
docker exec "$C" psql -U postgres -tAc "create extension if not exists orioledb;" >/dev/null 2>&1
docker cp "$HERE/sql/setup.sql" "$C":/tmp/setup.sql >/dev/null
docker cp "$HERE/sql/transfer_a0.sql" "$C":/tmp/ >/dev/null

# orioledb engine table in db 'postgres'; heap table in db 'heapdb'
docker exec "$C" psql -U postgres -d postgres -v persistence='' -v engine=orioledb \
  -c "$(sed 's/CREATE :persistence TABLE accounts/CREATE TABLE accounts/;s/balance bigint NOT NULL)/balance bigint NOT NULL) USING orioledb/' "$HERE/sql/setup.sql" 2>/dev/null)" >/dev/null 2>&1 \
  || docker exec "$C" bash -c "sed 's/CREATE :persistence TABLE accounts (id int PRIMARY KEY, balance bigint NOT NULL)/CREATE TABLE accounts (id int PRIMARY KEY, balance bigint NOT NULL) USING orioledb/' /tmp/setup.sql | psql -U postgres -d postgres" >/dev/null 2>&1
docker exec "$C" psql -U postgres -tAc "drop database if exists heapdb;" >/dev/null 2>&1
docker exec "$C" psql -U postgres -tAc "create database heapdb;" >/dev/null 2>&1
docker exec "$C" psql -U postgres -d heapdb -tAc "alter database heapdb set default_table_access_method='heap';" >/dev/null 2>&1
docker exec "$C" psql -U postgres -d heapdb -f /tmp/setup.sql >/dev/null 2>&1

echo "engine check: orioledb db -> $(docker exec "$C" psql -U postgres -d postgres -tAc "select amname from pg_am a join pg_class c on c.relam=a.oid where relname='accounts'")"
echo "             heapdb     -> $(docker exec "$C" psql -U postgres -d heapdb  -tAc "select coalesce((select amname from pg_am a join pg_class c on c.relam=a.oid where relname='accounts'),'heap')")"

echo; echo "===== transfer alpha=0, 8 clients (synchronous_commit=off) ====="
echo -n "orioledb : "; pgbench_run "$C" postgres 8 30 /tmp/transfer_a0.sql
echo -n "heap     : "; pgbench_run "$C" heapdb   8 30 /tmp/transfer_a0.sql

echo; echo "===== WAL bytes per transfer (fsync on, full_page_writes on) ====="
docker exec "$C" psql -U postgres -tAc "alter system set fsync=on; alter system set full_page_writes=on;" >/dev/null 2>&1
docker exec "$C" psql -U postgres -tAc "select pg_reload_conf();" >/dev/null 2>&1
wal_per() { # $1=db
  docker exec "$C" psql -U postgres -d "$1" -tAc "checkpoint;" >/dev/null 2>&1
  local s=$(docker exec "$C" psql -U postgres -d "$1" -tAc "select pg_current_wal_lsn()")
  docker exec -e PGPASSWORD=postgres "$C" pgbench -U postgres -h localhost -d "$1" -c 4 -j 4 -t 25000 -f /tmp/transfer_a0.sql >/dev/null 2>&1
  local e=$(docker exec "$C" psql -U postgres -d "$1" -tAc "select pg_current_wal_lsn()")
  docker exec "$C" psql -U postgres -d "$1" -tAc "select round(pg_wal_lsn_diff('$e','$s')/100000.0,1)||' bytes/txn'"
}
echo -n "orioledb : "; wal_per postgres
echo -n "heap     : "; wal_per heapdb
