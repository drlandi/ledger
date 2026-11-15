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
