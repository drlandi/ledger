# STATUS

Read this first when resuming work. Brief: crash-proof, concurrency-safe
transfer ledger in C, correctness shown by tests. Design: `DESIGN.md`.

## Done

- M0 foundations: reference model `tests/model.py`, shared invariant
  checker `tests/invariants.py`, their unit tests, repo skeleton.

## Next

- M1 single-threaded C core + differential test against the model.
- M2 locking (per-account locks in id order vs. global lock baseline).
- M3 write-ahead log, group commit, recovery, crash harness.
- M4 CLI, demo, benchmarks, README.

## Known issues

- None yet.

## Conventions

- C core in `src/`, public header `include/ledger.h`, 42 Norm checked
  with `norminette` (`make norm`). Test code is exempt.
- Tests are Python stdlib `unittest` in `tests/`; C test tools there too.
- Every test reports to `invariants.check_all`.
