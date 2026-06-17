"""
Pipelined Postgres stored-procedure benchmark using psycopg3 pipeline mode.
Matches SpacetimeDB's model: N workers x M inflight per pipeline batch.

Each worker opens one connection, loops sending batches of M queries in
pipeline mode (queries sent without waiting for results), then syncs.

Usage: python bench_pipeline.py [--workers 64] [--batch 40] [--seconds 60] [--alpha 0]
"""
import argparse, math, os, random, threading, time

import psycopg

ACCOUNTS = 100_000

def zipf_weights(n, alpha):
    weights = [1.0 / (i ** alpha) for i in range(1, n + 1)]
    total = sum(weights)
    cdf = []
    c = 0.0
    for w in weights:
        c += w / total
        cdf.append(c)
    return cdf

def zipf_pick(cdf):
    r = random.random()
    lo, hi = 0, len(cdf) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cdf[mid] < r:
            lo = mid + 1
        else:
            hi = mid
    return lo + 1

def pick_pair(alpha, cdf):
    if alpha == 0:
        a = random.randint(1, ACCOUNTS)
        b = random.randint(1, ACCOUNTS)
        while b == a:
            b = random.randint(1, ACCOUNTS)
        return a, b
    a = zipf_pick(cdf)
    b = zipf_pick(cdf)
    while b == a:
        b = zipf_pick(cdf)
    return a, b

def worker_fn(conninfo, alpha, cdf, batch_size, end_time, results, idx):
    completed = 0
    max_retries = 10
    with psycopg.connect(conninfo, autocommit=True,
                          prepare_threshold=1) as conn:
        while time.monotonic() < end_time:
            for attempt in range(max_retries):
                try:
                    with conn.pipeline():
                        for _ in range(batch_size):
                            f, t = pick_pair(alpha, cdf)
                            a = random.randint(1, 1000)
                            conn.execute(
                                "SELECT transfer(%s, %s, %s)", (f, t, a))
                    completed += batch_size
                    break
                except psycopg.errors.DeadlockDetected:
                    # Retry batch on deadlock (rare with canonical lock ordering)
                    continue
                except psycopg.errors.PipelineAborted:
                    # Pipeline aborted after deadlock, sync and retry
                    continue
    results[idx] = completed

def run(args):
    cdf = zipf_weights(ACCOUNTS, args.alpha) if args.alpha > 0 else None
    conninfo = (f"host={args.host} port={args.port} dbname={args.database} "
                f"user={args.user}")
    if args.password:
        conninfo += f" password={args.password}"

    results = [0] * args.workers
    end_time = time.monotonic() + args.seconds

    print(f"[bench] {args.workers} workers x {args.batch} batch, "
          f"alpha={args.alpha}, {args.seconds}s")

    t0 = time.monotonic()
    threads = []
    for i in range(args.workers):
        t = threading.Thread(target=worker_fn,
                             args=(conninfo, args.alpha, cdf, args.batch,
                                   end_time, results, i))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    elapsed = time.monotonic() - t0

    total = sum(results)
    tps = total / args.seconds
    print(f"[bench] completed = {total}, elapsed = {elapsed:.2f}s, tps = {tps:.0f}")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=64)
    p.add_argument("--batch", type=int, default=40)
    p.add_argument("--seconds", type=int, default=60)
    p.add_argument("--alpha", type=float, default=0)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5435)
    p.add_argument("--database", default="benchdb")
    p.add_argument("--user", default=os.environ.get("USER", "postgres"))
    p.add_argument("--password", default=None)
    args = p.parse_args()
    run(args)
