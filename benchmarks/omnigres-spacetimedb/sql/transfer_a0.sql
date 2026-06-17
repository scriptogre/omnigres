-- pgbench script: networked transfer, alpha=0 (uniform / uncontended)
\set from random(1, 100000)
\set to   random(1, 100000)
\set amt  random(1, 1000)
select transfer(:from, :to, :amt);
