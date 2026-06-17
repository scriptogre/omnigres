#!/usr/bin/env bash
# Run SpacetimeDB's OWN keynote-2 transfer benchmark on this machine, so the
# comparison is same-hardware / same-harness. Requires: docker, node>=22, pnpm, git.
set -uo pipefail
WORK="${WORK:-/tmp/stdb-bench}"
IMG=clockworklabs/spacetime:latest

echo "## 1. SpacetimeDB server (in-process compute+storage, tables in RAM)"
docker rm -f stdb >/dev/null 2>&1
docker run -d --name stdb -p 127.0.0.1:3000:3000 "$IMG" start --listen-addr 0.0.0.0:3000 >/dev/null
sleep 5

echo "## 2. extract the spacetime CLI to host (harness calls it directly)"
cid=$(docker create "$IMG"); sudo docker cp "$cid":/opt/spacetime /opt/spacetime; docker rm "$cid" >/dev/null
sudo ln -sf /opt/spacetime/spacetimedb-cli /usr/local/bin/spacetime
export PATH=/usr/local/bin:$PATH
spacetime --version | tail -1

echo "## 3. fetch the keynote-2 benchmark (sparse checkout)"
rm -rf "$WORK"; git clone --depth 1 --filter=blob:none --sparse \
  https://github.com/clockworklabs/SpacetimeDB.git "$WORK"
( cd "$WORK" && git sparse-checkout set templates/keynote-2 )
cd "$WORK/templates/keynote-2"

echo "## 4. repoint the workspace SDK dep to the published npm version (matches server)"
SDKVER=$(spacetime --version | grep -oE 'version [0-9.]+' | head -1 | awk '{print $2}')
sed -i "s/\"spacetimedb\": \"workspace:[^\"]*\"/\"spacetimedb\": \"$SDKVER\"/" package.json spacetimedb/package.json
pnpm install --no-frozen-lockfile
( cd spacetimedb && pnpm install --no-frozen-lockfile )

echo "## 5. publish the transfer module + seed 100k accounts (balance 1e9)"
spacetime publish -c -y --server http://127.0.0.1:3000 --module-path ./spacetimedb test-1
spacetime call --server http://127.0.0.1:3000 test-1 seed 100000 1000000000
spacetime sql --server http://127.0.0.1:3000 test-1 "select count(*) as n from accounts"

cat > .env <<EOF
STDB_URL=ws://127.0.0.1:3000
STDB_SERVER=http://127.0.0.1:3000
STDB_MODULE=test-1
STDB_MODULE_PATH=./spacetimedb
STDB_COMPRESSION=none
STDB_CONFIRMED_READS=1
BENCH_PIPELINED=1
MAX_INFLIGHT_PER_WORKER=40
USE_DOCKER=0
EOF

echo "## 6. run the bench (Node 22 explicitly; pnpm may pin an older node)"
# durable (confirmed reads on), alpha 0 and 1.5
for A in 0 1.5; do
  echo "--- SpacetimeDB durable, alpha=$A ---"
  /opt/node22/bin/node --import tsx src/cli.ts --connectors spacetimedb \
    --alpha "$A" --seconds 60 --concurrency 64 2>&1 | grep -iE "completed within|done"
done
echo "--- SpacetimeDB NO durability (confirmed_reads=0), alpha=0 ---"
STDB_CONFIRMED_READS=0 /opt/node22/bin/node --import tsx src/cli.ts --connectors spacetimedb \
  --alpha 0 --seconds 45 --concurrency 64 2>&1 | grep -iE "completed within|done"

echo
echo "TPS = 'completed within window' / seconds."
echo "Same-harness Postgres baseline: add a PG_URL to .env and run --connectors postgres_rpc."
