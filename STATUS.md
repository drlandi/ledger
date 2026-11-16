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

- Follow-up round, Part 1 (the three gaps; DESIGN section 9):
  - Gap 2 was a real defect: mid-log damage silently dropped
    acknowledged transfers. Fixed with a commit record per batch
    (new log format, D25/D26); recovery cuts only what one interrupted
    write explains and otherwise refuses with a reason.
    `tests/test_torn.py`.
  - Gap 3 found a second, smaller defect: state-dependent rejections
    were answered before durable. Fixed (D27). Reads are now checked
    in the crash harness (`explain`) and by `tests/gate.c`.
  - Gap 1: no defect found; `tests/gate.c` (fdatasync held by the
    test via `--wrap`) and 128-byte-buffer storms,
    `make test-groupcommit`.
  - `make mutants`: 65/65 (41 old, 24 new) plus a control per group.
    `make test` OK (about 8 min). `make crash-long` 1000/1000.
- Not yet done: Part 1b (plain-language section in DESIGN), Part 2
  (TLA+ model; `model/` holds an unchecked draft if present).

## Next

Open items, in order of value:
a batch tag per data record and a generation number in the commit
record (the two "known limit" tests in `tests/test_torn.py` would then
flip to "refuse"); a repair tool for a refused log; checkpoint + log
compaction; pre-allocated idempotency table; a real power-loss test in
a VM.

## Known issues / limits

- Real power loss is simulated (kill -9 + tail damage), not tested.
- A damaged last batch is cut even if it had been acknowledged; two
  narrower cases are pinned as `test_known_limit_*` (DESIGN 9).
- A refused log needs a person: there is no repair tool.
- Logs from before the commit-record format are not readable.
- Log, recovery time and idempotency memory grow without bound.
- `lg_count` / `lg_stats` do not wait for durability (diagnostics).
- In-memory throughput is capped near 4.5M/s by page faults on
  idempotency-table memory; per-account locks lose to the global lock
  on two hot accounts and single-threaded (DESIGN 5).
- `sync=process` slows down with many threads (DESIGN 7).
- 42 file headers are not stamped (needs the author's login).
- `tests/mutants.py` patterns are tied to the source text; the script
  checks them up front and names the stale one after a refactor.
- `make test` takes about 8 minutes; the slow part is
  `tests/test_torn.py` under ASan (1 MiB batches).

## Conventions

- C core in `src/`, public header `include/ledger.h`, 42 Norm checked
  with `norminette` (`make norm`). Test code is exempt.
- Tests are Python stdlib `unittest` in `tests/`; C test tools there too.
- Never delete scratch directories with `rm -rf` globs in this
  environment (blocked); use `mktemp -d`.
- Every test reports to `invariants.check_all`.
