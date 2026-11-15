"""Reference model of the ledger.

Single-threaded, in-memory, written to be obviously correct rather than
fast. The C core is tested against this file: same inputs must give the
same result codes and the same final state.

Python ints are arbitrary precision, so the int64 limits are enforced
explicitly here instead of being an accident of the machine word.
"""

OK = "OK"
DUPLICATE = "DUPLICATE"
INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
UNKNOWN_ACCOUNT = "UNKNOWN_ACCOUNT"
INVALID = "INVALID"
FULL = "FULL"

INT64_MAX = 2**63 - 1
INT64_MIN = -(2**63)
FLAG_OVERDRAFT = 1


class Model:
    def __init__(self, max_accounts=1024):
        self.max_accounts = max_accounts
        self.balances = []      # account id is the index
        self.flags = []
        self.initials = []      # balance each account was created with
        self.seeded = 0         # sum of all initial balances
        self.applied = {}       # transfer id -> (from, to, amount)
        self.log = []           # applied transfers, in order

    def create(self, initial, flags=0):
        """Returns (rc, account_id). The id is None unless rc is OK."""
        if initial < 0 or flags not in (0, FLAG_OVERDRAFT):
            return INVALID, None
        if self.seeded + initial > INT64_MAX:
            return INVALID, None
        if len(self.balances) >= self.max_accounts:
            return FULL, None
        self.balances.append(initial)
        self.flags.append(flags)
        self.initials.append(initial)
        self.seeded += initial
        return OK, len(self.balances) - 1

    def transfer(self, tid, src, dst, amount):
        if amount <= 0 or src == dst:
            return INVALID
        if tid in self.applied:
            if self.applied[tid] == (src, dst, amount):
                return DUPLICATE
            return INVALID
        n = len(self.balances)
        if not (0 <= src < n and 0 <= dst < n):
            return UNKNOWN_ACCOUNT
        overdraft = self.flags[src] & FLAG_OVERDRAFT
        if not overdraft and self.balances[src] < amount:
            return INSUFFICIENT_FUNDS
        if self.balances[src] - amount < INT64_MIN:
            return INVALID
        if self.balances[dst] + amount > INT64_MAX:
            return INVALID
        self.balances[src] -= amount
        self.balances[dst] += amount
        self.applied[tid] = (src, dst, amount)
        self.log.append((tid, src, dst, amount))
        return OK

    def balance(self, acc):
        """Returns (rc, balance). The balance is None unless rc is OK."""
        if not 0 <= acc < len(self.balances):
            return UNKNOWN_ACCOUNT, None
        return OK, self.balances[acc]
