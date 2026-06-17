-- Faithful port of SpacetimeDB's keynote-2 "transfer" benchmark to Postgres.
-- Works on vanilla Postgres, omnigres, or OrioleDB (set the table's USING clause).
--
-- Schema + data match SpacetimeDB exactly: 100,000 accounts, balance 1e9.
-- Pass -v engine=orioledb (or heap) and -v persistence=unlogged (or "") via psql,
-- or just edit the CREATE TABLE below.

\set ON_ERROR_STOP on
\if :{?persistence}
\else
  \set persistence ''
\endif

DROP TABLE IF EXISTS accounts;
-- e.g. CREATE UNLOGGED TABLE ... USING orioledb
CREATE :persistence TABLE accounts (id int PRIMARY KEY, balance bigint NOT NULL);
INSERT INTO accounts SELECT g, 1000000000 FROM generate_series(1, 100000) g;

-- ---------------------------------------------------------------------------
-- transfer(): verbatim logic of SpacetimeDB's `transfer` reducer.
-- Canonical (ascending-id) lock ordering prevents A->B / B->A deadlocks under
-- contention. SpacetimeDB serializes transactions so it never deadlocks; this
-- is the equivalent guarantee for Postgres' concurrent backends.
-- same_account / insufficient_funds are treated as no-ops (not a real transfer),
-- matching "count successful transfers" semantics.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION transfer(p_from int, p_to int, p_amount bigint)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE from_bal bigint;
BEGIN
  IF p_from = p_to     THEN RETURN; END IF;
  IF p_amount <= 0     THEN RETURN; END IF;
  IF p_from < p_to THEN
    SELECT balance INTO from_bal FROM accounts WHERE id = p_from FOR UPDATE;
    PERFORM balance              FROM accounts WHERE id = p_to   FOR UPDATE;
  ELSE
    PERFORM balance              FROM accounts WHERE id = p_to   FOR UPDATE;
    SELECT balance INTO from_bal FROM accounts WHERE id = p_from FOR UPDATE;
  END IF;
  IF from_bal < p_amount THEN RETURN; END IF;
  UPDATE accounts SET balance = balance - p_amount WHERE id = p_from;
  UPDATE accounts SET balance = balance + p_amount WHERE id = p_to;
END $$;

-- In-DB driver: loop N transfers in-process (NO client round-trips), COMMIT
-- each one (= one durable transaction per transfer, the SpacetimeDB analog).
-- Logic is inlined (not a PERFORM transfer() call) to avoid per-call overhead.
-- Run several of these concurrently (one per core+) to use all CPUs.
CREATE OR REPLACE PROCEDURE transfer_loop(n int) LANGUAGE plpgsql AS $$
DECLARE i int; f int; t int; fb bigint;
BEGIN
  FOR i IN 1..n LOOP
    f := 1 + (random()*99999)::int;
    t := 1 + (random()*99999)::int;
    IF f <> t THEN
      IF f < t THEN
        SELECT balance INTO fb FROM accounts WHERE id = f FOR UPDATE;
        PERFORM balance         FROM accounts WHERE id = t FOR UPDATE;
      ELSE
        PERFORM balance         FROM accounts WHERE id = t FOR UPDATE;
        SELECT balance INTO fb FROM accounts WHERE id = f FOR UPDATE;
      END IF;
      IF fb >= 100 THEN
        UPDATE accounts SET balance = balance - 100 WHERE id = f;
        UPDATE accounts SET balance = balance + 100 WHERE id = t;
      END IF;
    END IF;
    COMMIT;  -- one durable transaction per transfer
  END LOOP;
END $$;
