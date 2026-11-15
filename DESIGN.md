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
| 1 | 5,740,404 | 3,907,484 | 0.68x |
| 2 | 1,115,095 | 3,120,250 | 2.80x |
| 4 | 1,372,247 | 4,136,883 | 3.01x |
| 8 | 1,517,545 | 4,165,507 | 2.74x |

80% of traffic on 2 hot accounts:

| threads | global lock | per-account locks | ratio |
|---:|---:|---:|---:|
| 1 | 4,566,393 | 3,934,083 | 0.86x |
| 2 | 1,184,354 | 1,216,770 | 1.03x |
| 4 | 1,296,513 | 659,616 | 0.51x |
| 8 | 1,323,552 | 565,424 | 0.43x |

What the numbers say, without spin:

1. Single-threaded, the global lock wins (one uncontended mutex
   instead of three). Fine-grained locking has a fixed price.
2. With uniform traffic and more than one thread, per-account locks
   are about 3x faster. The global lock gets *slower* with threads
   (5.7M -> 1.1M): every transfer bounces the same lock between cores.
3. With traffic concentrated on two accounts, per-account locks are
   the *worse* design (0.43x at 8 threads). The hot accounts' locks are
   a global lock in disguise, and each transfer now pays for three
   locks and sleeps on a contended one while still holding its shard
   lock. Fine-grained locking only helps when the workload actually
   spreads out.
4. Per-account locking does not scale beyond ~4M/s here, and the cause
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
| D10 | Locking | Shard lock for the id, then per-account locks in ascending id order | (a) One global lock: simplest and fastest single-threaded, but measured 3x slower with uniform multi-threaded traffic. (b) Lock-free CAS on balances: cannot update two balances atomically without a transaction scheme. (c) try-lock and back off: livelock risk and harder to reason about than a fixed order. | Deadlock freedom follows from a one-line ordering rule that can be explained and checked. Measured trade-offs are in section 5, including the workload where this choice loses. |
| D11 | Hold the shard lock for the whole transfer | Yes | Reserve the id, release, apply, then confirm | A reservation needs an "in progress" state and a way for a concurrent duplicate to wait for it. Holding the lock is correct by construction; with 1024 shards the extra contention is small. |
| D12 | Consistent snapshot | Lock every account in order | A reader-writer "gate" taken shared by every transfer | The gate would put one shared cache line on the path of every transfer, taxing the hot path to speed up a rare audit. Cost accepted: a snapshot stalls transfers and is O(accounts). ThreadSanitizer's own deadlock detector cannot follow more than 64 held locks, so it is switched off (race detection stays on) for the scenarios with more than 60 accounts. |
| D13 | Account count without a lock | C11 atomic, slot initialised before count is raised | Taking `create_lock` in every transfer | Keeps account creation off the transfer path. `<stdatomic.h>` is part of the C11 compiler, no extra dependency. |

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
- Be ready to say where the design loses: single-threaded (0.68x) and
  with two hot accounts (0.43x at 8 threads). And that scaling was
  limited by memory page faults, found by measuring, not guessing.
- Interview questions: "Why can't this deadlock?" "What if a transfer
  needed three accounts?" (same rule: sort all of them.) "Why is the
  global lock slower with 2 threads than with 1?" (the lock's cache
  line and the data it protects migrate between cores on every
  operation.) "What does ThreadSanitizer not prove?" (anything about
  interleavings that did not occur in the run.)
