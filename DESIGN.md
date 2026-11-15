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
