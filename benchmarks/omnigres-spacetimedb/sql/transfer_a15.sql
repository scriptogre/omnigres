-- pgbench script: networked transfer, alpha=1.5 (Zipfian / ~80% contention).
-- pgbench's random_zipfian reproduces SpacetimeDB's power-law account selection.
\set from random_zipfian(1, 100000, 1.5)
\set to   random_zipfian(1, 100000, 1.5)
\if :from = :to
  \set to (:from % 100000) + 1
\endif
\set amt  random(1, 1000)
select transfer(:from, :to, :amt);
