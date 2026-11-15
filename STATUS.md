# STATUS

Read this first when resuming work. Brief: crash-proof, concurrency-safe
transfer ledger in C, correctness shown by tests. Design: `DESIGN.md`.

## Done

- M0 foundations: reference model `tests/model.py`, shared invariant
  checker `tests/invariants.py`, their unit tests, repo skeleton.
- M1 single-threaded C core (`src/`), differential test
  (`make test-diff`, 3000 seeds + 1000 under ASan/UBSan), mutation
  check (`make mutants`, 18/18 killed), Norm clean (`make norm`).
- M2 concurrency: shard lock -> account locks in id order
  (`src/lock.c`), global-lock baseline, stress tool `tests/stress.c`
  with watchdog, `make test-stress` (plain + TSan + ASan), race
  mutants (24/24 killed), `make bench`. Numbers in DESIGN section 5.
- M3 durability: WAL with CRC + length framing (`src/wal*.c`), group
  commit, fail-stop on I/O error, recovery (`src/recover.c`), exclusive
  file lock. Tests: `make test-recovery` (hand-damaged logs),
  `make test-crash` (kill -9 harness, disk-full, strace fdatasync
  count, TSan), `make crash-long` (1000 kills: passed, 1.13M transfers).
  CLI `build/ledger` exists (create/transfer/balance/verify/dump).

- M4 packaging: `make demo` (400 kills, 2,350,072 transfers, all
  invariants held), `make bench`, README with numbers and commands.
- M5 (stretch): optional hypothesis test with shrinking,
  `make test-property`. No TLA+ spec.

## Next

Nothing is required. Natural extensions, in order of value:
checkpoint + log compaction; pre-allocated idempotency table; TLA+
model of group commit and recovery; a real power-loss test in a VM.

## Known issues / limits

- Real power loss is simulated (kill -9 + tail damage), not tested.
- Log, recovery time and idempotency memory grow without bound.
- Mid-log corruption is detected, not repaired.
- In-memory throughput is capped near 4.5M/s by page faults on
  idempotency-table memory; per-account locks lose to the global lock
  on two hot accounts and single-threaded (DESIGN 5).
- `sync=process` slows down with many threads (DESIGN 7).
- 42 file headers are not stamped (needs the author's login).
- `tests/mutants.py` patterns are tied to the source text; the script
  checks them up front and names the stale one after a refactor.

## Conventions

- C core in `src/`, public header `include/ledger.h`, 42 Norm checked
  with `norminette` (`make norm`). Test code is exempt.
- Tests are Python stdlib `unittest` in `tests/`; C test tools there too.
- Every test reports to `invariants.check_all`.
