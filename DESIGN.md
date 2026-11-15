# DESIGN

How the ledger works, why it is built this way, and what was rejected.
Sections are added milestone by milestone; the decision log and the
"What Landi must understand" notes are at the end.

## 1. Scope

A transfer ledger: accounts hold integer cents, a transfer moves cents
from one account to another, and each transfer carries an idempotency
key. The project is about *demonstrated* correctness of that small core
under concurrency and crashes. It is not a bank: no users, no
authentication, no network, no currencies, no reporting.

## 2. Domain rules

These rules are implemented twice: obviously in `tests/model.py`
(Python, about 70 lines) and efficiently in `src/` (C). The two must
agree on every input.

Accounts

- An account id is a dense index assigned by the ledger (0, 1, 2, ...).
- A balance is an `int64` number of cents. Floating point never appears.
- A normal account may never go below zero. An account created with the
  `OVERDRAFT` flag may (it plays the role of the bank / outside world,
  so that funding a customer is itself a transfer and the total stays
  constant).
- An account is created with an initial balance >= 0 ("seeding"). This
  is the only way the total amount of money changes.

Transfer `(id, from, to, amount)`, checks in this exact order:

1. `amount <= 0` or `from == to` -> `INVALID`
2. `id` already applied: same `(from, to, amount)` -> `DUPLICATE`,
   anything different -> `INVALID`
3. `from` or `to` does not exist -> `UNKNOWN_ACCOUNT`
4. `from` is a normal account and `balance < amount` ->
   `INSUFFICIENT_FUNDS`
5. the debit would underflow or the credit overflow `int64` -> `INVALID`
6. otherwise apply both sides, remember the id -> `OK`

Only an applied (`OK`) transfer consumes its id. A rejected transfer
changes nothing and is not remembered, so the same id may be retried
later and succeed (see decision D3).

## 3. Invariants and the judge

| | Invariant | How the checker sees it |
|---|---|---|
| I1 | Conservation | sum of balances == sum of initial balances |
| I2 | No negative balance | in every snapshot, and after every entry of an ordered log |
| I3 | Exactly-once | no transfer id appears twice among applied transfers |
| I4 | Atomicity | balances == initial balances + every applied transfer taken whole |
| I5 | Durability | every acknowledged id is present after recovery, same parameters |
| I6 | Deterministic recovery | two recoveries of the same log give identical state |

`tests/invariants.py` is the only place these are checked. It takes
plain data (lists and dicts) and imports neither the model nor the C
core, so a bug in an implementation cannot hide inside the checker.
`tests/test_invariants.py` injects one violation per invariant and
requires the checker to name it; this is the evidence that a green run
means something.

## 4. The C core (M1)

| File | Contents |
|---|---|
| `include/ledger.h` | Public API and result codes |
| `src/ledger.c` | open / close |
| `src/account.c` | create, balance |
| `src/transfer.c` | transfer (the rule list of section 2), lookup |
| `src/idem.c` | idempotency table |
| `src/snapshot.c` | consistent snapshot, `lg_verify` self-check |

Idempotency table. `transfer id -> (from, to, amount)`, split into 1024
independent open-addressing hash tables ("shards"). The id is hashed
with the splitmix64 finaliser; the top 10 bits choose the shard and the
low bits the slot. Each shard doubles when it passes 70% full. The
split exists for M2 (one lock per shard); single-threaded it is just a
hash table. Room is reserved *before* a transfer has any effect, so an
allocation failure returns `ERROR` and leaves the ledger untouched.

Overflow. All checks are written so the check itself cannot overflow,
e.g. `dst->balance > INT64_MAX - amount` rather than
`dst->balance + amount > INT64_MAX`. Signed overflow in C is undefined
behaviour, not wrap-around, so "compute, then test" is already a bug.
The running total of seeded money is overflow-checked at account
creation, which guarantees the sum of all balances fits in an `int64`.

Evidence (M1):

- `make test-diff`: 3000 random sequences, 548,000 operations, C and
  model identical line by line and in final state; again for 1000 seeds
  with AddressSanitizer + UndefinedBehaviorSanitizer. Every result code
  is asserted to be reached thousands of times.
- `make mutants`: 18 one-line bugs planted in a copy of the core
  (off-by-one in the funds check, credit side dropped, id not
  remembered, hash probe skipping a slot, ...). The differential test
  kills 18/18.
- `make norm`: norminette passes on `src/` and `include/` apart from
  the file header (N1 below).

Found while breaking it:

- The first differential run failed (seed 673). Cause: the *generator*
  produced an amount of 2^63, which is not an `int64`; the driver's
  `sscanf` saturated it. A test bug, not a core bug, but it shows the
  comparison is live. Amounts are now clamped to the `int64` range.
- One mutant survived at first: with the `initial < 0` check deleted,
  negative initial balances were still rejected, by accident, because
  the next line (`seeded > INT64_MAX - initial`) overflows for a
  negative `initial` and happened to wrap to "reject". The explicit
  check is therefore not redundant: it is what keeps that line free of
  undefined behaviour. Mutants are now built with UBSan, which turns
  the accident into a crash, and the mutant dies.

### 42 Norm deviations

| # | Deviation | Why |
|---|---|---|
| N1 | No 42 file header | It embeds a student login and e-mail; left for the author to stamp. `make norm` filters only this message. |
| N2 | One `static const` global (`g_rc_names`) | Read-only name table; the Norm tolerates const globals (norminette emits a notice, not an error). |

## 5. Concurrency (M2)

### Locking strategy

Three kinds of lock, always taken in this order:

```
create_lock  ->  one idempotency-shard lock  ->  account locks, lowest id first
```

| Lock | Protects | Held by |
|---|---|---|
| `create_lock` | account creation, the seeded total | `lg_create`, `lg_snapshot` |
| shard lock (1024 of them, chosen by hash of the transfer id) | that shard's part of the idempotency table | `lg_transfer` for its whole duration, `lg_lookup` |
| account lock (one per account) | that account's balance | `lg_transfer` (both accounts), `lg_balance` (one), `lg_snapshot` (all) |

Why each invariant holds:

- I3 exactly-once. Two submissions of the same id hash to the same
  shard, so they are serialised by the shard lock. The lock is held
  from the lookup until the id has been inserted, so the second one
  must see the first one's entry. "Check then insert" cannot interleave.
- I1/I4 atomicity. Both account locks are held while the two balances
  change, so no other thread can see or act on a half-done transfer.
- I2 no negative balance. The funds check and the debit happen under
  the source account's lock; the balance cannot change in between.
- No deadlock. A deadlock is a cycle of threads each waiting for a
  lock held by the next. Every thread takes locks in the same global
  order (the line above; account locks by ascending id), and a thread
  holds at most one shard lock, so a cycle cannot form.
- Consistent snapshot. `lg_snapshot` holds `create_lock` and *every*
  account lock (ascending, the same order), so it copies one instant
  of the whole ledger. Cost: O(accounts) lock operations and it stalls
  all transfers meanwhile; it is an audit tool, not a hot path.

The account count is a C11 atomic. `lg_create` fills the new slot
completely and only then raises the count, so a transfer that sees the
new count sees a finished account. Transfers never take `create_lock`.

`LG_LOCK_GLOBAL` is the baseline: one mutex replaces the shard and
account locks. `LG_LOCK_NAIVE` takes the two account locks in
from/to order and is deliberately deadlock-prone; it exists only as a
negative control for the tests.

### Evidence (M2)

`make test-stress` runs `tests/test_stress.py` three times: plain
(8 threads x 150,000 operations per scenario), under ThreadSanitizer,
and under ASan/UBSan. Scenarios: 80% of traffic on 2 hot accounts with
every id submitted by 3 threads at once plus late replays and
changed-parameter replays; only 2 accounts; every id submitted by all
8 threads; accounts being created while transfers run; global-lock
mode; 4096 accounts. For each run the C tool records every submission
with its result and a few hundred consistent snapshots taken while the
workers run; the shared checker then verifies I1 and I2 on every
snapshot and I1-I4 on the final state, and that no `DUPLICATE` was ever
answered for a transfer that is not applied.

- Result: all scenarios pass in all three builds; ThreadSanitizer
  reports no data race.
- Deadlock watchdog: the tool exits with code 42 if no thread
  progresses for N seconds. Negative control: in `NAIVE` mode on two
  accounts the watchdog fires every time
  (`test_watchdog_catches_a_real_deadlock`), and ThreadSanitizer
  independently reports "lock-order-inversion" for that mode. The same
  two detectors stay silent for the real locking mode.
- `make mutants` now also plants six concurrency bugs (account locks
  removed, only one account locked, shard lock removed, global mode
  without its lock, snapshot without locks, account published before
  it is initialised). The stress test kills 6/6 (24/24 with M1's).

Honest scope: stress testing and TSan show the absence of races on
the interleavings that happened to occur. They are evidence, not
proof. The proof-shaped part is the lock-order argument above.

### Throughput: per-account locks vs. global lock

Machine: cloud VM, 4 vCPU Intel Xeon @ 2.30 GHz, in-memory ledger (no
log yet), 1,000,000 transfers per run, median of 5 runs, transfers per
second. Reproduce with `make bench`.

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

(Numbers from the final run of `make bench`; runs vary by 10-20% on
this shared VM, the ordering of the designs does not.)

What the numbers say, without spin:

1. Single-threaded, the global lock wins (one uncontended mutex
   instead of three). Fine-grained locking has a fixed price.
2. With uniform traffic and more than one thread, per-account locks
   are 2.6x to 3.9x faster. The global lock gets *slower* with threads
   (3.6M -> 1.1M): every transfer bounces the same lock between cores.
3. With traffic concentrated on two accounts, per-account locks are
   the *worse* design (0.46x at 8 threads). The hot accounts' locks are
   a global lock in disguise, and each transfer now pays for three
   locks and sleeps on a contended one while still holding its shard
   lock. Fine-grained locking only helps when the workload actually
   spreads out.
4. Per-account locking does not scale beyond ~4.5M/s here, and the cause
   is not locking. Investigation: the 2-thread run spends 1.15 s in
   the kernel against 0.18 s single-threaded, with only ~5,000 futex
   calls per million transfers (`strace -c`) and an identical number
   of page faults. The time goes to page faults on fresh memory for
   the idempotency table (every transfer stores 32 bytes), which are
   much more expensive when several threads fault at once in this VM.
   One-off experiment (not in the tree): pre-size each shard to 8192
   slots and touch the memory at open. Result with 4,000,000 transfers:
   per-account locks 3.5M / 5.3M / 8.5M per second at 1 / 2 / 4
   threads, global lock 5.8M / 1.5M / 1.9M. So the locking design
   scales with cores once memory is pre-touched; as shipped, the
   idempotency table's memory growth is the bottleneck. Pre-allocation
   was left out to keep the core small (see limitations).
5. An earlier version of this table was wrong: the benchmark tool
   itself shared an atomic id counter and a progress counter between
   threads, and that contention was being measured. The tool now uses
   per-thread id ranges and per-thread counters in benchmark runs.

## 6. Durability and recovery (M3)

### The log

The only persistent state is one append-only file, the write-ahead
log (WAL). Memory is rebuilt from it at open. Nothing in the file is
ever modified in place; the only non-append operation is cutting off a
damaged tail at recovery.

```
file    "LGWAL001"  record  record  record ...
record  len u32 | crc u32 | type u8 | body              little-endian
          len   size of type + body (the framing)
          crc   CRC-32 (zlib polynomial) of the len field + type + body
          type  1 = create    id u64, initial i64, flags u32        (len 21)
                2 = transfer  id u64, from u64, to u64, amount i64  (len 33)
```

One transfer is one record, so a transfer is on disk completely
(checksum matches) or not at all: atomicity across crashes (I4) is a
property of the format. The checksum covers the length field, and the
length is checked against the two legal values and against the bytes
actually present *before* it is used.

### Write path and group commit

```
lg_transfer:
  shard lock, account locks
    validate
    append record to the in-memory log buffer   -> LSN (file offset after it)
    change the two balances, remember the id
  release all locks
  wait until the log is durable up to that LSN  -> only now return OK
```

"Durable up to an LSN" is produced by group commit (`src/wal.c`): the
first thread that needs durability becomes the leader, takes the whole
buffer (its record and everyone else's), swaps in an empty one, and
does one `write` + `fdatasync` with no lock held. Threads arriving
meanwhile append to the fresh buffer and wait; when the leader
finishes they are woken and one of them leads the next batch. One disk
flush is shared by every transfer that arrived during the previous
one, which is why more client threads give more throughput.

Two rules make this safe:

1. *Nothing is acknowledged before it is durable.* `OK` is returned
   only after the record's LSN is durable. The same holds for
   `DUPLICATE` (it claims "already applied", so the original must be
   durable first), for `lg_balance` and for snapshots (a reader must
   not see money a crash could take back).
2. *Log order respects dependency order.* The record is appended while
   the account locks are still held. For any single account, the order
   of records in the log is therefore the order in which transfers
   really touched it. The log reaches the disk as a prefix, so if a
   transfer survives a crash, every transfer it depended on (the one
   that funded it) is earlier in the log and survives too. This is
   what allows the locks to be released *before* the slow disk wait.

### fsync policy

| Mode | Acknowledge after | Survives | Cost |
|---|---|---|---|
| `LG_SYNC_FULL` (default) | `write` + `fdatasync` | process crash and power loss, to the extent the drive honours flush requests | one disk flush per batch |
| `LG_SYNC_PROCESS` | `write` only | process crash (`kill -9`): the data is in the kernel page cache. Not power loss or kernel crash. | no flush |

`fdatasync` rather than `fsync`: it flushes the data and the file
size, which is everything recovery needs, and skips timestamps.
When the log file is created its directory is fsync'ed too, otherwise
the file itself could vanish after a power cut.

I/O errors are fail-stop. If `write` or `fdatasync` fails, the log is
marked failed, every waiting and every later operation returns
`ERROR`, and nothing is retried. Retrying `fsync` after a failure is
unsafe on Linux: the kernel may already have dropped the dirty pages
and report success the second time. The process must be restarted,
and recovery decides from the file what really happened.

### Recovery

`lg_open` reads the file and replays records through `lg_create` and
`lg_transfer` themselves (with logging off). There is no second
implementation of the rules for recovery to disagree with, and replay
is a pure function of the file's bytes (I6). Replay stops at the first
record that is incomplete or fails its checksum. Then:

| What recovery finds | Meaning | Action |
|---|---|---|
| File ends exactly after a valid record | clean | continue |
| Invalid bytes at the end, at most 1 MiB | torn tail: a batch was being written at the crash | cut the file at the last valid record, fsync, continue |
| More than 1 MiB of invalid bytes | not a torn tail: one batch is at most 1 MiB (`LG_WAL_BUF`) | refuse to open (`CORRUPT`), file untouched |
| A record with a correct checksum that the rules reject (overdraw, repeated id, wrong account id ...) | this program never wrote that there: corruption or a bug | refuse to open (`CORRUPT`), file untouched |
| First bytes are not a prefix of the magic | not a ledger | refuse to open, file untouched |
| At most 8 bytes, and either a prefix of the magic or all zeros | crash while creating the file (nothing was ever acknowledged) | start a new ledger |

The cut is fsync'ed before anything is appended, so discarded bytes
cannot reappear behind new records.

Every record cut off by recovery was unacknowledged: an acknowledged
record belongs to a batch whose `fdatasync` returned, and batches are
written strictly one after another.

### Evidence (M3)

`make test-recovery` (also with the CLI built under ASan/UBSan) uses
hand-built logs (`tests/test_recovery.py`; the format is implemented a
second time in Python, `tests/walfmt.py`, with zlib's CRC):

- a log written by Python is read by C and vice versa, byte-identical
- a 10-record log cut at every one of its 371 byte offsets: recovery
  yields exactly the records wholly before the cut, the file is cut
  there, and a second recovery changes nothing
- every single byte of that log corrupted three ways (1,086 cases):
  exactly the records before the damaged one survive
- zeros, random bytes, a partial record appended: ignored and removed
- more than 1 MiB of damage, eleven kinds of intact-but-impossible
  record, foreign files: refused, file byte-for-byte untouched

`make test-crash` runs the crash harness (`tests/crash_harness.py`)
four ways (full sync, process sync, 96 threads on 6 accounts, and a
build whose commit buffer holds only 3 records), plus:

- disk refuses to grow the log (`RLIMIT_FSIZE`): the worker gets
  `ERROR`, then `ERROR` for every further call; after restart every
  acknowledged transfer is present
- the log path under ThreadSanitizer: no race reported
- `strace` counts system calls: > 100 `fdatasync` in FULL mode, zero
  in PROCESS mode (skipped if strace is not installed)

One harness iteration: start the workload on the ledger left by the
previous victim; `SIGKILL` it after a random delay (one kill in five
within the first 4 ms, i.e. during recovery or account creation);
collect the acks the worker wrote after each returned call; damage
the log tail beyond the highest offset any ack reported as durable
(truncate at a random byte, truncate to exactly the durable offset,
flip bytes, append rubbish or zeros); recover three times; hand the
recovered balances, the log parsed by Python, and every ack since the
ledger was created to the shared checker. On top of I1-I6 it requires
that C and the Python reader agree on where the valid log ends and
that recovery never cuts below a durable offset.

Long run on the final code, `make crash-long`
(`python3 tests/crash_harness.py --iterations 1000 --threads 64`):

```
crash harness: 1000 kill -9, sync=full, 64 threads, 112 s
  transfers that survived in the logs : 1269501
  transfer calls that returned        : 2352372 (OK=1257390, DUPLICATE=1020254, INSUFFICIENT_FUNDS=59895, INVALID=14833)
  tail damage injected                : append=136, flip=125, none=383, truncate=113, truncate+append=125, truncate_to_floor=118
  bytes of torn tail cut by recovery  : 243509
  I1-I6 held after every recovery     : yes (1000 of 1000)
RESULT: 1,269,501 transfers committed by 64 threads across 1000 kill -9 crashes;
        after every one of the 1000 recoveries all six invariants held.
```

`make mutants`: 17 more planted bugs, all killed (41/41 in total).
Durability: OK returned before durable; batch marked durable before
it is written; write error ignored; fdatasync skipped; record logged
after the account locks are released; torn tail not cut; log position
not restored; DUPLICATE answered before the original is durable;
two processes allowed on one log.
Recovery: checksum not verified; checksum not covering the length;
length not bounded by the file; impossible record skipped; unlimited
tail damage accepted; foreign file overwritten; torn tail left in
place; account ids not checked on replay.

Found while breaking it:

- The first 1000-kill run failed at kill 780: "log header damaged".
  The worker had been killed before it wrote the 8-byte header, and
  the harness then appended its rubbish at offset 0, i.e. it damaged
  bytes below the durable floor, which its own rules forbid (a
  precedence mistake in one condition). Harness bug, fixed. It raised
  a real question though: what if a crash during creation leaves the
  header as zeros (file size on disk, data not)? The ledger used to
  refuse such a file. Nothing can have been acknowledged at that
  point, so it is now treated as a new ledger; anything else that is
  not the magic is still refused untouched.
- Apart from that, no failure of the core was observed in M3. The
  mutants are the evidence that the tests are able to fail.
- One planted bug survived at first ("replay ignores a rejected
  record") in the crash group. It was a bad mutant, not a blind test:
  a log produced by a correct writer never contains a rejected record,
  so the crash tests cannot tell. The same bug is killed by the
  hand-built logs of `test_recovery.py`, where it now lives.
- "fdatasync skipped" is invisible to any kill -9 test, because the
  page cache outlives the process. Only the `strace` test catches it.

### What is not verified

- Real power loss. `SIGKILL` plus tail damage is a simulation: it
  assumes that data covered by a returned `fdatasync` survives and
  that damage is confined to the unsynced tail. A drive that lies
  about flushes, or a kernel bug, is outside what these tests can see.
- Corruption in the middle of the log (bit rot in old records).
  Recovery treats the first bad record as the end of the log; if at
  most 1 MiB follows, the valid records after it are cut off silently
  (recovery reports the number of bytes discarded, nothing more).
  Checksums detect the damage; nothing here repairs it.
- A stale but intact record appearing exactly at the tail (old disk
  contents resurfacing). Records carry no sequence number or link to
  their predecessor, so such a record would be accepted if the rules
  allow it.

## 7. Packaging and evidence (M4)

- CLI `build/ledger` (`cli/ledger_cli.c`): `create`, `transfer`,
  `balance`, `verify`, plus `dump` for the tests. Numbers are parsed
  strictly (whole string, in range, never as floating point); tested
  with `1.5`, `1e3`, `0x10`, 2^63, padded strings.
- `make demo`: the crash harness with 64 threads, kill delays of
  150-450 ms, 400 kills. Result on the reference machine:

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

- `make bench`: the tables in section 5 and the durable throughput
  below (log on, per-account locks, 64 accounts, 40,000 transfers per
  run, median of 3):

| threads | sync=process | sync=full | transfers per fdatasync |
|---:|---:|---:|---:|
| 1 | 1,576,703 | 7,447 | 1.0 |
| 4 | 133,976 | 12,786 | 2.4 |
| 16 | 187,344 | 27,122 | 8.4 |
| 64 | 166,353 | 49,223 | 27.5 |
| 256 | 171,357 | 60,704 | 46.8 |

  `sync=full` scales with client threads because of group commit: one
  transfer per flush with 1 thread, ~47 per flush with 256.
  `sync=process` is the opposite: 1.58M/s alone, ~170,000/s with
  several threads, because each waiter performs its own tiny `write()`
  and the log lock is handed from thread to thread. Not tuned.
- A fresh clone was built and tested from scratch (see STATUS).

## 8. Property test with shrinking (M5, stretch)

`make test-property` (optional, needs `hypothesis`) states the same
property as `test_diff.py` but lets hypothesis generate and *shrink*
the sequences. Against the planted off-by-one in the funds check it
reports this minimal case instead of "seed 673, operation 172":

```
ops=[('c', 0, 1), ('c', 2, 0), ('t', 0, 1, 0, 2)]
     bank          2 cents       move exactly 2 cents out -> wrongly refused
```

Found while building it: the first two versions of the strategy
passed against that same planted bug, even with 3000 examples.
Measured cause: independent random draws produced a *successful*
transfer in under 1% of cases, so the funds check was never at its
boundary. The strategy now starts every case with funded accounts and
uses small amounts. Lesson kept in the file's comments: a generator
has to be checked against a known bug, exactly like any other test.

No TLA+ model was written; the lock-order and log-order arguments in
sections 5 and 6 are prose, not machine-checked.

## Decision log

| # | Decision | Chosen | Rejected | Why |
|---|---|---|---|---|
| D1 | Test framework | Python stdlib `unittest` | pytest | A stranger can run `make test` with a bare Python 3; nothing to install. |
| D2 | Account ids | Dense index assigned by the ledger, fixed capacity set at open | Caller-chosen 64-bit ids | A caller-chosen id needs a concurrent hash map for accounts. A dense index is an array lookup with no lock. Mapping names to ids belongs to a layer above. |
| D3 | Which outcomes consume an idempotency key | Only `OK` | Also remembering rejections | The key exists to guarantee a transfer is *applied* at most once. A rejection has no effect, so re-evaluating it is safe, and it keeps rejections out of the durable log. Cost: a retry of a rejected transfer can succeed later if funds arrived. Stated, not hidden. |
| D4 | What `DUPLICATE` reports | `DUPLICATE` means "this exact transfer was already applied" | Returning the stored original code | Follows from D3: the stored outcome is always `OK`, so `DUPLICATE` carries the full information. |
| D5 | Overflow result code | `INVALID` | A sixth domain code | The brief fixes five domain codes; an amount that cannot be represented is an invalid request. |
| D6 | How money enters | Initial balance at creation, plus an overdraft "bank" account | A separate `deposit` call | One fewer mutation path to make atomic and durable. Funding through the bank account is ordinary double entry. |
| D7 | Codes beyond the brief's five | `FULL` (account table full), `ERROR` (I/O or memory; see M3), `CORRUPT` (log fails validation) | Overloading `INVALID` | These describe the environment, not the request. Mixing them would hide real faults. |
| D8 | Idempotency table | Sharded open-addressing hash table, grows by doubling, never forgets | Sorted array; one big table; expiring keys | O(1) lookups, and the shards give M2 a natural lock unit. Never forgetting keys means memory grows with history (32 bytes per transfer); a real system would expire keys by age. Listed as a limitation. |
| D9 | How to test the tests | Mutation check (`make mutants`) | Trusting coverage numbers | A test that cannot fail proves nothing. Planting known bugs and requiring a failure measures exactly that. |
| D10 | Locking | Shard lock for the id, then per-account locks in ascending id order | (a) One global lock: simplest and fastest single-threaded, but measured about 3x slower with uniform multi-threaded traffic. (b) Lock-free CAS on balances: cannot update two balances atomically without a transaction scheme. (c) try-lock and back off: livelock risk and harder to reason about than a fixed order. | Deadlock freedom follows from a one-line ordering rule that can be explained and checked. Measured trade-offs are in section 5, including the workload where this choice loses. |
| D11 | Hold the shard lock for the whole transfer | Yes | Reserve the id, release, apply, then confirm | A reservation needs an "in progress" state and a way for a concurrent duplicate to wait for it. Holding the lock is correct by construction; with 1024 shards the extra contention is small. |
| D12 | Consistent snapshot | Lock every account in order | A reader-writer "gate" taken shared by every transfer | The gate would put one shared cache line on the path of every transfer, taxing the hot path to speed up a rare audit. Cost accepted: a snapshot stalls transfers and is O(accounts). ThreadSanitizer's own deadlock detector cannot follow more than 64 held locks, so it is switched off (race detection stays on) for the scenarios with more than 60 accounts. |
| D13 | Account count without a lock | C11 atomic, slot initialised before count is raised | Taking `create_lock` in every transfer | Keeps account creation off the transfer path. `<stdatomic.h>` is part of the C11 compiler, no extra dependency. |
| D14 | Persistence | Append-only WAL, state rebuilt by replay | Updating a balance file in place; an embedded database | In-place updates need their own crash protocol (which is a WAL again). Append-only makes "what can a crash damage?" answerable: only the tail. |
| D15 | When to acknowledge | After the record is durable, with group commit | (a) fsync per transfer under the locks: correct, a few thousand transfers/s at most. (b) Acknowledge first, flush in the background: fast, but an acknowledged transfer can vanish, which breaks I5. | Group commit keeps the strict guarantee and shares each disk flush among all waiting threads. |
| D16 | Locks during the disk wait | Released ("early lock release") | Holding account locks until durable | Holding them would serialise every transfer on a hot account behind a disk flush. Safe because the record is appended under the locks, so log order respects dependencies, and because readers also wait for durability. |
| D17 | I/O error handling | Fail-stop, never retry | Retry the write or fsync | After a failed fsync Linux may discard the dirty pages and report success on retry. Stopping and recovering from the file is the only state one can vouch for. |
| D18 | Torn tail vs. corruption | Cut at most one batch (1 MiB) of invalid tail; refuse anything larger, and refuse intact records the rules reject | Always cut at the first bad record; or never cut | "Always cut" can silently delete arbitrarily many good records behind one flipped bit. "Never cut" cannot restart after an ordinary crash. The 1 MiB bound is exactly what one interrupted batch can explain. |
| D19 | Recovery code path | Replay through the normal `lg_create` / `lg_transfer` | A dedicated fast apply routine | One implementation of the rules. A valid log must replay with `OK` for every record; anything else is treated as corruption. |
| D20 | Reads and durability | `lg_balance`, snapshots and `DUPLICATE` wait for the log to be durable up to the moment they looked | Returning in-memory state immediately | Otherwise a caller could see a balance, the machine could crash, and the money would be gone again: an observable lie even though no `OK` was lost. Cost: a read may wait for one disk flush. |
| D21 | Checksum | CRC-32 (zlib) | CRC-32C, a cryptographic hash | Detects torn and garbled records, which is the job. Being the zlib polynomial lets Python's `zlib.crc32` verify the C code independently. Not a defence against deliberate tampering. |
| D22 | No snapshots / log compaction | Out of scope | Periodic checkpoint + log truncation | Recovery time and file size grow with history. A checkpoint needs its own atomic-replace protocol and tests; left out to keep M0-M4 solid. Listed as a limitation. |
| D23 | One process per log | `flock` on the log file, second opener refused | Trusting the operator | Two writers would interleave records and each would hold a stale in-memory state. The kernel releases the lock on any death, kill -9 included. |
| D24 | M5 choice | Property test with shrinking (hypothesis, optional) | TLA+ spec | Cheaper, runs against the real code, and reuses the model. A TLA+ spec of group commit + recovery would be the natural next step. |

## What Landi must understand

### M0 - model and judge

- Key decision: correctness is defined *before* the C code exists, as
  six invariants plus a 70-line Python model. The C core is never
  trusted on its own word; it is compared against these.
- Why two things and not one: the model answers "what should this
  operation return?" (differential testing). The checker answers "is
  this state legal at all?" and also works where no model can follow,
  for example with eight threads racing, where the order is unknown.
- Failure it prevents: tests that pass because they assert what the
  code does rather than what it should do. The checker tests prove each
  invariant can actually fail.
- I4 is checked without knowing the order: addition commutes, so final
  balances must equal initials plus all applied transfers. A
  half-applied transfer breaks that equation whatever the interleaving.
- Interview question: "Your checker passed. How do you know the checker
  works?" Answer: `test_invariants.py` feeds it one hand-made violation
  per invariant (money created, debit without credit, id applied twice,
  acked transfer lost, ...) and requires the right invariant to be
  named. Also "Why is a balanced-but-wrong state (I4) not caught by
  conservation (I1)?" Because moving 10 cents to the wrong account
  keeps the sum intact.

### M1 - single-threaded core

- Key decision: the C core is judged by *differential testing*. A
  generator produces hostile operation sequences (tiny id spaces to
  force replays, amounts at balance-1 / balance / balance+1, `INT64`
  edges, unknown accounts, a full account table); both implementations
  run them; any differing line fails with the seed and the operation.
- Failure it prevents: the classic "I tested the cases I thought of".
  The generator finds cases nobody listed; the model says what the
  right answer is without a human writing expected values.
- The order of checks in a transfer is part of the contract. If C
  checked "unknown account" before "duplicate id" the results would
  differ only in rare inputs; the differential test pins the order.
- Overflow checks are rearranged subtractions, never "add and look":
  signed overflow is undefined behaviour in C, and the compiler may
  delete a check that can only be true after overflow.
- Money enters only at account creation, and the running total is
  overflow-checked there, so "sum of balances" can never itself
  overflow.
- Interview question: "How do you know your tests would notice a bug?"
  Answer: `make mutants` plants 18 real bugs one at a time and all 18
  are caught; and tell the story of the mutant that survived because of
  undefined behaviour. Also: "Why is `a + b > MAX` a wrong overflow
  check in C?"

### M2 - concurrency

- Key decision: one lock per account, always taken lowest id first,
  after the lock of the idempotency shard for the transfer id.
- Failure it prevents (1): lost updates. Two threads doing
  `balance = balance - x` on the same account without a lock both read
  the old value; one debit vanishes and money is created.
- Failure it prevents (2): deadlock. Thread 1 moves A->B and holds A;
  thread 2 moves B->A and holds B; each waits for the other forever.
  Sorting the two ids means both threads ask for A first. One waits,
  nobody holds B while waiting for A, no cycle.
- Failure it prevents (3): double apply. Two threads submit the same
  id at the same moment; both look it up, both find nothing, both
  apply. The shard lock makes "look up, apply, remember" one step.
- The four conditions for deadlock (mutual exclusion, hold and wait,
  no preemption, circular wait): lock ordering removes the fourth.
- A test that cannot fail is worthless, so there are negative
  controls: `NAIVE` mode really deadlocks and the watchdog reports it;
  removing any single lock makes the stress test fail.
- Be ready to say where the design loses: single-threaded (0.85x) and
  with two hot accounts (0.46x at 8 threads). And that scaling was
  limited by memory page faults, found by measuring, not guessing.
- Interview questions: "Why can't this deadlock?" "What if a transfer
  needed three accounts?" (same rule: sort all of them.) "Why is the
  global lock slower with 2 threads than with 1?" (the lock's cache
  line and the data it protects migrate between cores on every
  operation.) "What does ThreadSanitizer not prove?" (anything about
  interleavings that did not occur in the run.)

### M3 - durability

- Key decision: a write-ahead log with group commit. A transfer is
  acknowledged only after its record is on disk; many threads share
  one `fdatasync`.
- Failure it prevents (1): the lost acknowledgement. The client was
  told `OK`, the power fails, the transfer is gone. Rule: never
  answer before `fdatasync` returned for that record.
- Failure it prevents (2): the half transfer. Debit on disk, credit
  not. One transfer is one checksummed record; a torn record fails
  its CRC and is cut off, so it is all or nothing.
- Failure it prevents (3): the orphan. Transfer B spends money that
  transfer A delivered; after the crash B exists and A does not.
  Cannot happen: A's record was appended before B's (B needed the
  account lock A held), and the file survives as a prefix.
- Know the difference between `write` (data in the kernel's page
  cache: survives `kill -9`, not a power cut) and `fsync`/`fdatasync`
  (data on the device). `kill -9` tests alone cannot distinguish the
  two; that is why there is a `strace` test and why the harness also
  destroys the unsynced tail.
- Know "fsyncgate": after a failed fsync, retrying is not safe, so the
  ledger stops.
- Recovery = replay through the same code as normal operation, stop at
  the first bad record, cut the tail (max 1 MiB), fsync the cut.
- Interview questions: "A client got a timeout, not an answer. What
  now?" (Retry with the same id: `OK` if the first attempt was lost,
  `DUPLICATE` if it was applied. That is what the idempotency key is
  for.) "Why is it safe to release the locks before the fsync?"
  "What does your crash test not prove?" (real power loss, lying
  disks, mid-log bit rot; see "What is not verified".) "Why refuse a
  record with a valid checksum?"

### M4 - packaging and evidence

- Key decision: the headline number comes from one command
  (`make demo`) and every claim in the README names the command that
  produced it. No number is quoted that was not printed by a run.
- Read the demo output precisely: "transfers committed" counts records
  that survived in the logs after recovery; "calls that returned"
  counts acknowledgements the dead processes managed to record. About
  half of the calls are `DUPLICATE` because every id is deliberately
  submitted twice.
- Why 60,000 transfers/s with fsync but 7,400/s single-threaded: the
  disk does ~6,000 flushes per second either way; group commit puts
  many transfers into each flush. Throughput = flushes/s x batch size.
- Be exact about what `kill -9` proves. It proves the code path
  "acknowledge only after write+fdatasync returned, recover from any
  prefix". It does not prove the drive kept its promise.
- Benchmarks are from a shared 4-vCPU cloud VM; a laptop SSD may take
  1-5 ms per `fdatasync` instead of 0.17 ms, and durable throughput
  scales down with it. Say that before being asked.
- Interview questions: "How would you make it faster?" (pre-allocate
  the idempotency table; batch `write()` in process mode; several
  log files or a dedicated log thread.) "How would you bound recovery
  time?" (periodic checkpoint: write a snapshot to a new file, fsync,
  atomic rename, then start a new log.) "What would you need for two
  machines?" (replicating the log: a different, much larger project.)

### M5 - property test

- hypothesis generates operation lists and, on failure, shrinks them
  to a minimal list. Same oracle as M1: the Python model.
- The lesson worth telling: the first strategy could not find a
  planted bug, because random inputs rarely reached a successful
  transfer. Random testing is only as good as the states it reaches;
  measure that (here: count applied transfers per example).
