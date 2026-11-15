"""Differential test: random operation sequences are run through the C
core (build/driver) and the Python model; every result line and the
final state must be identical. The final C state also goes to the
shared invariant checker.

Environment: DIFF_SEEDS (default 3000), DRIVER (default build/driver).
"""

import os
import random
import subprocess
import unittest
from collections import Counter

import model as M
from invariants import Observation, check_all

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER = os.environ.get("DRIVER", os.path.join(ROOT, "build", "driver"))
SEEDS = int(os.environ.get("DIFF_SEEDS", "3000"))
TIMEOUT = int(os.environ.get("DIFF_TIMEOUT", "300"))
U64 = 2**64 - 1
EDGE = [0, 1, -1, 2, M.INT64_MAX, M.INT64_MAX - 1, M.INT64_MIN,
        M.INT64_MIN + 1, 2**62]


class Case:
    """One seed: builds the input text and the expected output text."""

    def __init__(self, seed, ops=None):
        self.rng = random.Random(seed)
        self.ops = ops or self.rng.choice([30, 120, 400])
        self.m = M.Model(self.rng.choice([1, 2, 3, 6, 12]))
        self.inp = ["r %d" % self.m.max_accounts]
        self.exp = ["OK"]
        self.ids = set()
        self.rcs = Counter()
        self.id_space = self.rng.choice([8, 60, 5000, U64])

    def account(self):
        r = self.rng.random()
        n = len(self.m.balances)
        if r < 0.90 and n:
            return self.rng.randrange(n)
        if r < 0.97:
            return n + self.rng.randrange(3)
        return self.rng.choice([U64, U64 - 1, 2**32, 2**63])

    def amount(self, src):
        r = self.rng.random()
        if r < 0.60:
            return self.rng.randrange(1, 500)
        if r < 0.82 and src < len(self.m.balances):
            near = self.m.balances[src] + self.rng.randrange(-2, 3)
            return max(M.INT64_MIN, min(M.INT64_MAX, near))
        if r < 0.90:
            return self.rng.choice(EDGE)
        return self.rng.randrange(M.INT64_MIN, M.INT64_MAX + 1)

    def op_create(self):
        r = self.rng.random()
        initial = self.rng.randrange(0, 2000)
        if r < 0.15:
            initial = self.rng.choice(EDGE)
        flags = self.rng.choice([0, 0, 0, 1])
        if self.rng.random() < 0.05:
            flags = self.rng.choice([2, 3, 255, 2**32 - 1])
        rc, acc = self.m.create(initial, flags)
        self.inp.append("c %d %d" % (initial, flags))
        self.exp.append("OK %d" % acc if rc == M.OK else rc)
        self.rcs["create " + rc] += 1

    def op_transfer(self):
        r = self.rng.random()
        if r < 0.25 and self.m.log:
            # Replay an applied transfer, sometimes with one field changed.
            tid, src, dst, amt = self.rng.choice(self.m.log)
            k = self.rng.randrange(6)
            if k == 0:
                src = self.account()
            elif k == 1:
                dst = self.account()
            elif k == 2:
                amt = self.amount(src)
        else:
            tid = self.rng.randrange(self.id_space + 1)
            src = self.account()
            dst = self.account()
            if dst == src and self.rng.random() < 0.9:
                dst = self.account()
            amt = self.amount(src)
        rc = self.m.transfer(tid, src, dst, amt)
        self.ids.add(tid)
        self.inp.append("t %d %d %d %d" % (tid, src, dst, amt))
        self.exp.append(rc)
        self.rcs["transfer " + rc] += 1

    def op_balance(self):
        acc = self.account()
        rc, bal = self.m.balance(acc)
        self.inp.append("b %d" % acc)
        self.exp.append("OK %d" % bal if rc == M.OK else rc)

    def finish(self):
        m = self.m
        self.inp.append("d")
        cells = " ".join("%d:%d" % bf for bf in zip(m.balances, m.flags))
        self.exp.append(("D %d %d OK %s"
                         % (len(m.balances), m.seeded, cells)).rstrip())
        self.lookups = sorted(self.ids)
        for tid in self.lookups:
            self.inp.append("l %d" % tid)
            if tid in m.applied:
                self.exp.append("L %d %d %d" % m.applied[tid])
            else:
                self.exp.append("L -")

    def build(self):
        for _ in range(self.ops):
            r = self.rng.random()
            if r < 0.08 or not self.m.balances:
                self.op_create()
            elif r < 0.90:
                self.op_transfer()
            else:
                self.op_balance()
        self.finish()
        return self


def run_driver(cases):
    text = "\n".join("\n".join(c.inp) for c in cases) + "\n"
    out = subprocess.run([DRIVER], input=text, capture_output=True,
                         text=True, timeout=TIMEOUT)
    if out.returncode != 0:
        raise AssertionError("driver exit %d: %s"
                             % (out.returncode, out.stderr[-2000:]))
    return out.stdout.split("\n")


def judge_c_state(case, got):
    """Feeds what the C core reported (not the model) to the checker."""
    n = len(case.lookups)
    dump = got[len(got) - n - 1].split()
    cells = [c.split(":") for c in dump[4:]]
    applied = []
    for tid, line in zip(case.lookups, got[len(got) - n:]):
        if line != "L -":
            src, dst, amt = line.split()[1:]
            applied.append((tid, int(src), int(dst), int(amt)))
    check_all(Observation(balances=[int(c[0]) for c in cells],
                          flags=[int(c[1]) for c in cells],
                          initials=list(case.m.initials),
                          applied=applied))


class Differential(unittest.TestCase):
    def compare(self, cases):
        lines = run_driver(cases)
        pos = 0
        for c in cases:
            got = lines[pos:pos + len(c.exp)]
            pos += len(c.exp)
            if got != c.exp:
                for i, (g, e) in enumerate(zip(got, c.exp)):
                    if g != e:
                        self.fail("seed %s op %d `%s`: C says %r, model "
                                  "says %r" % (c.seed, i, c.inp[i], g, e))
                self.fail("seed %s: output length differs" % c.seed)
            judge_c_state(c, got)

    def test_random_sequences_match_model(self):
        rcs = Counter()
        total = 0
        for start in range(0, SEEDS, 250):
            cases = []
            for seed in range(start, min(start + 250, SEEDS)):
                c = Case(seed).build()
                c.seed = seed
                cases.append(c)
                rcs.update(c.rcs)
                total += c.ops
            self.compare(cases)
        print("\n  differential: %d seeds, %d operations, all identical"
              % (SEEDS, total))
        print("  " + ", ".join("%s=%d" % kv for kv in sorted(rcs.items())))
        # The generator must actually reach every outcome.
        for key in ("create OK", "create INVALID", "create FULL",
                    "transfer OK", "transfer DUPLICATE", "transfer INVALID",
                    "transfer INSUFFICIENT_FUNDS",
                    "transfer UNKNOWN_ACCOUNT"):
            self.assertGreater(rcs[key], SEEDS // 20, key)

    def test_idempotency_table_growth(self):
        """Many distinct ids force every shard to grow several times."""
        rng = random.Random(7)
        c = Case(0)
        c.seed = "growth"
        c.m = M.Model(4)
        c.inp = ["r 4"]
        for init, fl in ((0, 1), (0, 0), (0, 0)):
            c.inp.append("c %d %d" % (init, fl))
            c.exp.append("OK %d" % c.m.create(init, fl)[1])
        for i in range(300000):
            tid = rng.choice([i, rng.randrange(U64 + 1), i << 40])
            src, dst = rng.sample(range(3), 2)
            amt = rng.randrange(1, 50)
            c.exp.append(c.m.transfer(tid, src, dst, amt))
            c.inp.append("t %d %d %d %d" % (tid, src, dst, amt))
            c.ids.add(tid)
        c.finish()
        self.assertGreater(len(c.m.applied), 100000)
        self.compare([c])


if __name__ == "__main__":
    unittest.main()
