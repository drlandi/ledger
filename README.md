# ledger

A small money-transfer ledger in C whose correctness is demonstrated
by tests that try to break it: random transfers from many threads,
the process killed with `kill -9` at random moments, the unsynced end
of its log file deliberately damaged, and after every restart the
books must still balance to the cent.

It is a correctness-focused core, not a banking product: no network,
no UI, no dependencies beyond libc and pthreads.

```
make test     # everything, about 2 minutes
make demo     # the headline result, about 3 minutes
```

## The headline result

`make demo` on the machine described under "Benchmarks":

```
DEMO_OUTPUT
```

What one "kill" means: 64 threads submit random transfers (every id
twice at the same moment, plus replays of ids from before earlier
crashes, some with changed amounts). After a random delay the process
gets `SIGKILL`. The harness then damages the part of the log that was
never confirmed durable (cuts it at a random byte, flips bytes,
appends rubbish), restarts the ledger, and checks the invariants
below against everything the dead processes had been told.

## What it guarantees

A transfer is `(id, from, to, amount)`; `id` is the idempotency key.
Amounts are integer cents in an `int64`; there is no floating point
anywhere, and overflow is detected and rejected.

| Result | Meaning |
|---|---|
| `OK` | applied, and durable on disk |
| `DUPLICATE` | this exact transfer was already applied; nothing changed |
| `INSUFFICIENT_FUNDS` | the source would go below zero; nothing changed |
| `UNKNOWN_ACCOUNT` | no such account; nothing changed |
| `INVALID` | amount <= 0, same account twice, would overflow, or a known id with different parameters |

| | Invariant |
|---|---|
| I1 | Conservation: the sum of all balances only changes when an account is created with an initial balance |
| I2 | No normal account is ever negative (an account flagged `overdraft` may be; it models the outside world) |
| I3 | Exactly-once: an id is applied at most once, however often and from however many threads it is sent |
| I4 | Atomicity: a transfer happens completely or not at all, also across a crash |
| I5 | Durability: a transfer that returned `OK` is there after a crash; one that did not return is completely there or completely absent |
| I6 | Recovery is deterministic: replaying the same log twice gives the same state |

## How it works, in one paragraph each

**Rules.** `tests/model.py` is a 70-line Python reference model of the
rules. The C core must agree with it on every input.

**Concurrency.** A transfer locks the idempotency-table shard of its
id, then the two accounts in ascending id order. One fixed lock order
means no deadlock; the shard lock makes "look up id, apply, remember
id" a single step.

**Durability.** Every create and transfer is one checksummed,
length-framed record in an append-only write-ahead log. A transfer
returns `OK` only after its record has been written and `fdatasync`ed.
Threads share disk flushes (group commit). Recovery replays the log
through the same code that handles live requests, stops at the first
damaged record, and cuts off the torn tail.

`DESIGN.md` has the full reasoning, a decision log (what was rejected
and why), the test evidence per milestone, and a list of what is not
verified.

## Evidence per invariant

All checks go through one shared checker, `tests/invariants.py`, which
sees only plain data and is itself tested by injecting one violation
per invariant (`tests/test_invariants.py`).

| Test (command) | What it does | Invariants |
|---|---|---|
| `make test-model` | model unit tests; checker catches injected violations | checker itself |
| `make test-diff` | 3000 random hostile sequences (548,000 operations): C core and model must give identical results and final state; again under ASan/UBSan | rules, I1-I4 single-threaded |
| `make test-stress` | 8 threads, hot accounts, every id submitted concurrently by several threads, accounts created mid-run; live snapshots; plain, ThreadSanitizer, ASan; deadlock watchdog with a negative control | I1-I4 under concurrency |
| `make test-recovery` | hand-damaged logs: cut at every byte, every byte corrupted, rubbish appended, impossible records | I4, I6, recovery rules |
| `make test-crash` | `kill -9` harness in four configurations; disk-full fail-stop; `fdatasync` counted with strace | I1-I6 across crashes |
| `make crash-long` | 1000 kills | I1-I6 across crashes |
| `make mutants` | plants 41 bugs in a copy of the core, one at a time; each must make a test fail | are the tests able to fail? |
| `make norm` | norminette on the core | 42 Norm |

Results of these on the reference machine are quoted in `DESIGN.md`
(sections 4-6) and summarised here:

```
EVIDENCE
```

## Benchmarks

Machine: BENCH_MACHINE. Reproduce with `make bench` (about 3 minutes).
Throughput on your disk will differ, mostly through the cost of
`fdatasync`.

BENCH_TABLES

Reading the numbers:

- With the log on and `sync=full`, throughput is set by the disk:
  each batch costs one `fdatasync`, and throughput rises with the
  number of client threads because more transfers share each flush.
- In memory, per-account locks are about 3x faster than one global
  lock for spread-out traffic with several threads, slower for a
  single thread (three locks instead of one), and slower when nearly
  all traffic hits two accounts. `DESIGN.md` section 5 explains each
  case and what limits scaling.

## Command line

```
build/ledger LOG create INITIAL_CENTS [overdraft]    -> OK <account id>
build/ledger LOG transfer ID FROM TO AMOUNT_CENTS    -> OK | DUPLICATE | ...
build/ledger LOG balance ACCOUNT                     -> OK <cents>
build/ledger LOG verify                              -> self-check and recovery summary
build/ledger LOG dump                                -> machine-readable state
```

```
$ build/ledger my.wal create 0 overdraft     # account 0: the bank
OK 0
$ build/ledger my.wal create 0               # account 1: a customer
OK 1
$ build/ledger my.wal transfer 1001 0 1 2500
OK
$ build/ledger my.wal transfer 1001 0 1 2500 # same request again: no effect
DUPLICATE
$ build/ledger my.wal transfer 1001 0 1 9999 # same id, different request
INVALID
$ build/ledger my.wal balance 1
OK 2500
$ build/ledger my.wal verify
OK: 2 accounts, 0 cents seeded, 3 log records replayed, 0 torn bytes discarded
```

Each invocation opens the log, replays it, executes one command and
exits; the CLI is a demonstration, the library (`include/ledger.h`) is
the product.

## Repository map

| Path | Contents |
|---|---|
| `include/ledger.h` | public API |
| `src/` | the core, 42 Norm style (accounts, transfers, idempotency table, locks, log, recovery) |
| `cli/ledger_cli.c` | command-line front end |
| `tests/model.py` | reference model |
| `tests/invariants.py` | the shared invariant checker |
| `tests/test_*.py` | test suites (Python `unittest`, standard library only) |
| `tests/driver.c`, `stress.c`, `crash_worker.c` | C test tools driven by the Python tests |
| `tests/crash_harness.py` | the kill -9 harness |
| `tests/walfmt.py` | the log format, implemented a second time in Python |
| `tests/mutants.py` | mutation check |
| `tests/bench.py` | benchmarks |
| `DESIGN.md` | design, decision log, evidence, interview notes |
| `STATUS.md` | current state of the work |

## Requirements

Linux, a C11 compiler (gcc or clang) with pthreads, GNU make,
Python 3.8+ (standard library only). Optional: `strace` (one test is
skipped without it), `norminette` (`make norm`). ThreadSanitizer and
AddressSanitizer come with the compiler.

## Known limitations

- Real power loss is simulated, not tested (see `DESIGN.md`,
  "What is not verified").
- The log is never compacted: file size and recovery time grow with
  history, and so does the memory of the idempotency table (32 bytes
  per applied transfer, never expired).
- Damage in the middle of the log is detected, not repaired; valid
  records after the damage are lost if at most 1 MiB follows it.
- One process at a time per log (enforced with `flock`); no
  replication, no network access.
- Account ids are dense indexes assigned by the ledger; the capacity
  is fixed at open.
- Little-endian machines only (checked at open).
