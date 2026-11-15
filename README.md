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
make demo     # the headline result, about 2.5 minutes (DEMO_KILLS=40 for 20 s)
```

## The headline result

`make demo` on the machine described under "Benchmarks":

```
Running 400 rounds of: 64 threads transfer money, kill -9, damage the log tail, recover, check.
crash harness: 400 kill -9, sync=full, 64 threads, 146 s
  transfers that survived in the logs : 2350072
  transfer calls that returned        : 4664377 (OK=2344812, DUPLICATE=2204102, INSUFFICIENT_FUNDS=62698, INVALID=52765)
  tail damage injected                : append=51, flip=49, none=159, truncate=52, truncate+append=38, truncate_to_floor=51
  bytes of torn tail cut by recovery  : 67575
  I1-I6 held after every recovery     : yes (400 of 400)
RESULT: 2,350,072 transfers committed by 64 threads across 400 kill -9 crashes;
        after every one of the 400 recoveries all six invariants held.
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
| `make test-property` | optional, needs `hypothesis`: same property as test-diff, with shrinking of failing cases | rules |

Results of these on the reference machine are quoted in `DESIGN.md`
(sections 4-6) and summarised here:

```
make test-model     26 tests OK
make test-diff      3000 seeds, 548,000 operations identical; 1000 seeds under ASan/UBSan identical
make test-stress    7 scenarios x 3 builds (plain, TSan, ASan) OK; watchdog fires on the planted deadlock
make test-recovery  11 tests OK (371 cut points, 1,086 corrupted bytes), also under ASan/UBSan
make test-crash     180 kills in 4 configurations + disk-full + lock + TSan + strace: OK
make crash-long     1000 kills, 1,269,501 transfers committed, I1-I6 held 1000 of 1000
make demo           400 kills, 2,350,072 transfers committed, I1-I6 held 400 of 400
make mutants        41/41 planted bugs caught
make norm           OK (only the 42 file header is not stamped)
```

## Benchmarks

Machine: cloud VM, 4 vCPU Intel Xeon @ 2.30 GHz, 15 GB RAM, virtio disk with ext4
(`fdatasync` of a small append takes about 0.17 ms), Linux 6.18, gcc 13.3 `-O2`. Reproduce with `make bench` (about 3 minutes).
Throughput on your disk will differ, mostly through the cost of
`fdatasync`.

Durable throughput (write-ahead log on, per-account locks, 64 accounts,
40,000 transfers per run, median of 3, transfers per second):

| threads | sync=process | sync=full | transfers per fdatasync |
|---:|---:|---:|---:|
| 1 | 1,576,703 | 7,447 | 1.0 |
| 4 | 133,976 | 12,786 | 2.4 |
| 16 | 187,344 | 27,122 | 8.4 |
| 64 | 166,353 | 49,223 | 27.5 |
| 256 | 171,357 | 60,704 | 46.8 |

In-memory core, no log (1,000,000 transfers per run, median of 5):

Uniform traffic over 1024 accounts:

| threads | global lock | per-account locks | ratio |
|---:|---:|---:|---:|
| 1 | 3,578,089 | 3,033,960 | 0.85x |
| 2 | 1,095,747 | 2,825,295 | 2.58x |
| 4 | 1,151,296 | 4,502,880 | 3.91x |
| 8 | 1,225,545 | 4,115,066 | 3.36x |

80% of traffic on 2 hot accounts:

| threads | global lock | per-account locks | ratio |
|---:|---:|---:|---:|
| 1 | 3,472,291 | 3,255,081 | 0.94x |
| 2 | 974,608 | 1,386,881 | 1.42x |
| 4 | 1,374,951 | 667,905 | 0.49x |
| 8 | 1,208,245 | 559,679 | 0.46x |

Reading the numbers:

- With the log on and `sync=full`, throughput is set by the disk:
  each batch costs one `fdatasync`. One thread gets one transfer per
  flush (7,447/s); 256 threads share each flush among ~47 transfers
  (60,704/s). That is group commit doing its job.
- `sync=process` is fastest with a single thread (1.58M/s) and drops
  to ~170,000/s with several: every waiting thread issues its own
  small `write()` and threads hand the log lock back and forth. It was
  measured, not tuned; the durable mode is the one this project is
  about.
- In memory, per-account locks are 2.6x to 3.9x faster than one global
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
