# omnigres / Postgres / OrioleDB vs SpacetimeDB — transfer benchmark

A faithful, same-hardware reproduction of SpacetimeDB's keynote-2 "transfer"
benchmark, used to answer: **how much of SpacetimeDB's headline advantage is the
database engine, and how much is the client↔server network round-trip that
in-database compute (omnigres' thesis) eliminates?**

## TL;DR

On **identical hardware** (everything below ran on the same 4-vCPU / 15 GB box),
the ~100× gap in SpacetimeDB's marketing collapses to roughly **parity** once the
Postgres logic runs *in-process* instead of over an ORM + network:

| Config (4 cores, 100k accounts) | α=0 TPS | α=1.5 TPS | durable? |
|---|---:|---:|:--:|
| **SpacetimeDB** (their harness, confirmed-reads) | 79,900 | 81,300 | yes (pipelined) |
| **SpacetimeDB**, confirmed-reads off | ~73,000 | – | no |
| **In-DB Postgres/OrioleDB**, UNLOGGED (no durability) | **96,000** | – | no |
| **In-DB Postgres/OrioleDB**, RAM-backed WAL | **87,000** | – | crash-only |
| In-DB Postgres/OrioleDB, durable on slow disk (group commit) | 28,000 | – | yes (power) |
| Networked stored-proc (pgbench, 1 round-trip/txn) | 21,000 | 14,900 | relaxed |
| their published Node+ORM+Postgres (on a **24-core** box!) | 9,905 | 961 | – |

**Conclusions**
1. The headline "100×" is almost entirely the **network round-trip**, not the
   engine. Run the logic in-DB (stored procedure / omnigres) and Postgres is in
   SpacetimeDB's league.
2. With both **memory-resident** (SpacetimeDB always is; Postgres with
   `shared_buffers` ≥ dataset + UNLOGGED/RAM-WAL), in-DB Postgres **matches or
   beats** SpacetimeDB (96k / 87k vs 73–80k).
3. SpacetimeDB's *genuine* architectural win is **durable throughput on slow
   storage**: 80k durable on a disk that only does 1,170 fsync/s, via deep
   pipelined commit (~68 txns/fsync). Postgres group-commit gets ~28k there
   (~24 txns/fsync); it needs fast storage or batching to match.
4. **OrioleDB ≈ heap** at this scale (4 cores, in-RAM). Its real wins (it writes
   **3.3× less WAL** here) need the regime its own docs use: 64 cores +
   10–100 GB data, where WAL-insertion / buffer-cache contention dominate.

## The benchmark

A bank transfer between two accounts (read both, check funds, debit, credit),
ported **verbatim** from SpacetimeDB's `transfer` reducer
(`templates/keynote-2/rust_module/src/lib.rs`). 100,000 accounts, initial balance
1,000,000,000. Account selection is uniform (α=0) or Zipfian (α=1.5, ~80%
contention) — reproduced with pgbench's built-in `random_zipfian`.

One subtlety that matters: naive A→B / B→A transfers **deadlock** under Zipfian
skew (Postgres collapsed to 22 TPS). The fix is **canonical lock ordering** (lock
the lower account id first); SpacetimeDB serializes transactions so it never
deadlocks — this is the equivalent guarantee. See `sql/setup.sql`.

## Environment these numbers came from

- 4 vCPU, 15 GB RAM, shared cloud sandbox. **Disk: only ~1,170 fsync/s**
  (`pg_test_fsync`; a real NVMe does 10k–100k+). This slow disk is why durable-on-disk
  numbers are low and variable — treat them as relative, not absolute.
- omnigres image `ghcr.io/omnigres/omnigres-17` (PostgreSQL 17.10),
  OrioleDB image `orioledb/orioledb:latest-pg17` (PostgreSQL 17.9),
  SpacetimeDB `clockworklabs/spacetime` v2.6.0.

## How to replicate

```bash
cd benchmarks/omnigres-spacetimedb

./run-postgres.sh        # omnigres: networked vs in-DB, across durability tiers
./run-orioledb.sh        # OrioleDB vs heap: throughput + WAL-bytes-per-txn
./run-spacetimedb.sh     # SpacetimeDB's own keynote-2 harness, same box
```

Each script is self-contained (pulls its image, configures, seeds, runs). Numbers
scale with cores — on a 24-core box expect ~5–6× these figures across the board.

## The optimizations applied (and why)

**Server config** (`sql/optimized.conf`, appended to `postgresql.conf` because the
image pins `shared_buffers` there; container needs `--shm-size=2g`):
- `shared_buffers=2GB` — cache the whole ~750 MB dataset in RAM (no read I/O).
- `synchronous_commit=off` — don't block the commit on WAL fsync (biggest write knob).
- `fsync=off`, `full_page_writes=off` — max-throughput, non-durable tier only.
- `wal_level=minimal`, `autovacuum=off`, large `checkpoint_timeout`/`max_wal_size` —
  remove background/replication overhead during the run.
- `commit_delay=150` + `commit_siblings=5` — **group commit** for the durable tier.
- `UNLOGGED` tables — zero WAL (fastest, non-durable).

**Application level** (the bigger wins):
- **Run logic in-DB** (PL/pgSQL stored procedure) — kills the per-query network
  round-trip. ~21k networked → ~87–96k in-process. This is omnigres' whole point.
- **Canonical lock ordering** — eliminates deadlock storms under contention.
- **Parallel in-process backends** — one `CALL transfer_loop(N)` per core+, so all
  CPUs are used with effectively zero per-transfer network cost.

## Why "durable = 3k" was wrong, and the durability ladder

Durable throughput is bounded by `fsync_rate × (transactions batched per fsync)`.
On this 1,170-fsync/s disk:

| committers (group commit) | durable TPS |
|---:|---:|
| 4 | 3,034 |
| 16 | 9,290 |
| 32 | 15,358 |
| 64 (+`commit_delay`) | 26,667 |

So "3k" was just 4 committers (≈no batching). More committers → more amortization.
Remove the disk entirely (RAM-backed WAL, tmpfs) and you hit the **CPU ceiling,
87k**, *with* per-transaction durability against a process crash (not power loss).
SpacetimeDB reaches 80k durable on the *slow* disk because its pipelining keeps
~2,560 requests in flight, batching ~68 transactions per fsync — deeper than
Postgres' practical backend count allows.

**Levers to go beyond 87k:** more cores (near-linear); app-level batching of K
transfers per commit (the SQLite-163k trick); a fast NVMe (raises the power-durable
ceiling); deeper pipelining. The binding limit here, once off the bad disk, is the
4-core CPU budget — not durability.

## Files

| File | What |
|---|---|
| `sql/setup.sql` | accounts table + `transfer()` + in-DB `transfer_loop()` (the verbatim reducer logic) |
| `sql/transfer_a0.sql`, `sql/transfer_a15.sql` | pgbench scripts (uniform / Zipfian) |
| `sql/optimized.conf` | the "make it fast" Postgres config, with durability tiers |
| `lib.sh` | parallel in-DB driver + pgbench helpers |
| `run-postgres.sh`, `run-orioledb.sh`, `run-spacetimedb.sh` | one-shot reproduction scripts |
