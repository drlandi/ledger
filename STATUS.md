# STATUS

Read this first when resuming work. Brief: crash-proof, concurrency-safe
transfer ledger in C, correctness shown by tests. Design: `DESIGN.md`.

## Done

- M0 foundations: reference model `tests/model.py`, shared invariant
  checker `tests/invariants.py`, their unit tests, repo skeleton.
- M1 single-threaded C core (`src/`), differential test
  (`make test-diff`, 3000 seeds + 1000 under ASan/UBSan), mutation
  check (`make mutants`, 18/18 killed), Norm clean (`make norm`).

## Next

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
