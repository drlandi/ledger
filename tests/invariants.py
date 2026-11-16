"""The single shared invariant checker ("the judge").

Every test hands what it observed to check_all() as an Observation.
The checker knows nothing about the C core or the Python model: it
only looks at plain data (balances, lists of transfers, sets of ids),
so a bug in either implementation cannot hide inside it.

Fields left as None are skipped, because not every test can observe
everything (a stress test has no crash, so nothing to say about I5).

I1 conservation   sum(balances) == sum of initial balances
I2 non-negative   no normal account below zero (in a snapshot, and at
                  every prefix of an ordered log)
I3 exactly-once   no transfer id applied twice
I4 atomicity      balances are exactly initials + whole transfers
I5 durability     every acknowledged id is present after recovery
I6 determinism    two recoveries of the same log give identical state
"""

from dataclasses import dataclass
from typing import Optional

FLAG_OVERDRAFT = 1


class InvariantViolation(AssertionError):
    pass


@dataclass
class Observation:
    balances: Optional[list] = None   # balance per account id
    flags: Optional[list] = None      # flags per account id
    initials: Optional[list] = None   # initial balance per account id
    applied: Optional[list] = None    # [(tid, src, dst, amount)] applied
    ordered: bool = False             # is `applied` in apply order?
    acked: Optional[dict] = None      # tid -> (src, dst, amount) acked
    recovery_a: object = None         # state dump of one recovery
    recovery_b: object = None         # state dump of a second recovery


def _i1(o):
    if o.balances is None or o.initials is None:
        return []
    have = sum(o.balances)
    want = sum(o.initials)
    if have != want:
        return ["I1 conservation: sum of balances %d != seeded %d"
                % (have, want)]
    return []


def _i2_snapshot(o):
    if o.balances is None or o.flags is None:
        return []
    out = []
    for i, bal in enumerate(o.balances):
        if bal < 0 and not o.flags[i] & FLAG_OVERDRAFT:
            out.append("I2 non-negative: account %d has balance %d"
                       % (i, bal))
    return out


def _i2_log_prefixes(o):
    """Replays an ordered log and checks I2 after every single step."""
    if not o.ordered or o.applied is None:
        return []
    if o.initials is None or o.flags is None:
        return []
    bal = list(o.initials)
    for n, (tid, src, dst, amount) in enumerate(o.applied):
        bal[src] -= amount
        bal[dst] += amount
        if bal[src] < 0 and not o.flags[src] & FLAG_OVERDRAFT:
            return ["I2 non-negative: after log entry %d (id %d) "
                    "account %d has balance %d" % (n, tid, src, bal[src])]
    return []


def _i3(o):
    if o.applied is None:
        return []
    seen = set()
    for tid, _, _, _ in o.applied:
        if tid in seen:
            return ["I3 exactly-once: transfer id %d applied twice" % tid]
        seen.add(tid)
    return []


def _i4(o):
    """Order-free: addition commutes, so the final balances must equal
    the initial ones plus every applied transfer taken whole."""
    if o.applied is None or o.balances is None or o.initials is None:
        return []
    if len(o.balances) != len(o.initials):
        return ["I4 atomicity: %d accounts but %d initial balances"
                % (len(o.balances), len(o.initials))]
    bal = list(o.initials)
    n = len(bal)
    for tid, src, dst, amount in o.applied:
        if amount <= 0 or src == dst or not (0 <= src < n and 0 <= dst < n):
            return ["I4 atomicity: malformed applied transfer id %d" % tid]
        bal[src] -= amount
        bal[dst] += amount
    for i in range(n):
        if bal[i] != o.balances[i]:
            return ["I4 atomicity: account %d is %d, but whole transfers "
                    "explain %d" % (i, o.balances[i], bal[i])]
    return []


def _i5(o):
    if o.acked is None or o.applied is None:
        return []
    present = {t[0]: (t[1], t[2], t[3]) for t in o.applied}
    out = []
    for tid, params in o.acked.items():
        if tid not in present:
            out.append("I5 durability: acked transfer id %d is missing"
                       % tid)
        elif params is not None and present[tid] != params:
            out.append("I5 durability: acked transfer id %d came back "
                       "as %r, was acked as %r"
                       % (tid, present[tid], params))
        if len(out) >= 5:
            break
    return out


def _i6(o):
    if o.recovery_a is None and o.recovery_b is None:
        return []
    if o.recovery_a != o.recovery_b:
        return ["I6 determinism: two recoveries of the same log differ"]
    return []


CHECKS = (_i1, _i2_snapshot, _i2_log_prefixes, _i3, _i4, _i5, _i6)


def violations(o):
    out = []
    for check in CHECKS:
        out.extend(check(o))
    return out


def check_all(o):
    """Raises InvariantViolation listing every broken invariant."""
    found = violations(o)
    if found:
        raise InvariantViolation("; ".join(found))
