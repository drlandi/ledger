"""Concurrency stress: many threads, hot accounts, duplicate ids
submitted at the same moment. Everything the C tool observed goes to
the shared invariant checker.

Environment: STRESS (default build/stress), STRESS_OPS (operations per
thread, default 150000), STRESS_THREADS (default 8).
"""

import os
import subprocess
import tempfile
import unittest
from collections import Counter

from invariants import Observation, check_all

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRESS = os.environ.get("STRESS", os.path.join(ROOT, "build", "stress"))
OPS = int(os.environ.get("STRESS_OPS", "150000"))
THREADS = int(os.environ.get("STRESS_THREADS", "8"))


def run(args, timeout=600):
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "out")
        cmd = [STRESS, "--out", out] + [str(a) for a in args]
        env = dict(os.environ)
        if "--accounts" in args and args[args.index("--accounts") + 1] > 60:
            # ThreadSanitizer's own deadlock detector aborts when one
            # thread holds more than 64 locks, which a snapshot of a
            # large ledger does on purpose. Race detection stays on;
            # deadlocks in these runs are left to the watchdog.
            env["TSAN_OPTIONS"] = "detect_deadlocks=0"
        res = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, env=env)
        lines = []
        if os.path.exists(out):
            with open(out) as f:
                lines = f.read().split("\n")
        return res, lines


def judge(lines):
    """Parses the tool's record and hands it to the checker.
    Returns counters for the test to report."""
    initials, flags, final = [], [], None
    snaps = []
    ok, acked = [], {}
    rcs = Counter()
    for line in lines:
        p = line.split()
        if not p:
            continue
        if p[0] == "T":
            t = (int(p[1]), int(p[2]), int(p[3]), int(p[4]))
            rcs[p[5]] += 1
            if p[5] == "OK":
                ok.append(t)
            if p[5] in ("OK", "DUPLICATE"):
                # Both answers claim "this exact transfer is applied".
                acked.setdefault(t[0], t[1:])
                if acked[t[0]] != t[1:]:
                    raise AssertionError("id %d acknowledged with two "
                                         "different parameter sets" % t[0])
        elif p[0] == "I":
            initials.append(int(p[2]))
            flags.append(int(p[3]))
        elif p[0] == "S":
            snaps.append([int(v) for v in p[2:]])
        elif p[0] == "F":
            final = [int(v) for v in p[1:]]
    # I1 + I2 at every moment the auditor looked.
    for bal in snaps:
        n = len(bal)
        check_all(Observation(balances=bal, flags=flags[:n],
                              initials=initials[:n]))
    # I1..I4 on the final state; I5's check doubles as "no DUPLICATE
    # answer without a matching applied transfer".
    check_all(Observation(balances=final, flags=flags, initials=initials,
                          applied=ok, acked=acked))
    rcs["snapshots"] = len(snaps)
    rcs["ids_submitted_more_than_once_and_applied"] = sum(
        1 for c in Counter(t[0] for t in ok).values() if c > 1)
    return rcs


class Stress(unittest.TestCase):
    def stress(self, name, args):
        res, lines = run(["--threads", THREADS, "--ops", OPS] + args)
        self.assertEqual(res.returncode, 0, res.stderr[-2000:])
        self.assertIn("verify=OK", res.stdout)
        rcs = judge(lines)
        print("\n  %s: %s\n    %s" % (
            name, res.stdout.strip(),
            ", ".join("%s=%d" % kv for kv in sorted(rcs.items()))))
        return rcs

    def test_hot_accounts_and_concurrent_duplicates(self):
        rcs = self.stress("hot+dup", [
            "--accounts", 16, "--hot", 2, "--hot-pct", 80,
            "--dup", 3, "--replay-pct", 10, "--seed", 11])
        for key in ("OK", "DUPLICATE", "INSUFFICIENT_FUNDS", "INVALID"):
            self.assertGreater(rcs[key], 100, key)
        self.assertGreater(rcs["snapshots"], 10)

    def test_two_accounts_only(self):
        """Every transfer fights over the same two locks."""
        self.stress("two-accounts", [
            "--accounts", 2, "--hot", 2, "--hot-pct", 100,
            "--dup", 2, "--replay-pct", 5, "--seed", 12])

    def test_every_id_submitted_by_all_threads(self):
        rcs = self.stress("same-id-storm", [
            "--accounts", 8, "--hot", 8, "--hot-pct", 0,
            "--dup", THREADS, "--seed", 13])
        self.assertGreater(rcs["DUPLICATE"], rcs["OK"])

    def test_accounts_created_while_transfers_run(self):
        rcs = self.stress("growing", [
            "--accounts", 256, "--hot", 4, "--hot-pct", 30, "--grow", 1,
            "--dup", 2, "--replay-pct", 5, "--seed", 14])
        self.assertGreater(rcs["UNKNOWN_ACCOUNT"], 0)

    def test_global_lock_mode(self):
        self.stress("global-lock", [
            "--mode", "global", "--accounts", 16, "--hot", 2,
            "--hot-pct", 80, "--dup", 3, "--replay-pct", 10, "--seed", 15])

    def test_many_accounts_wide_spread(self):
        self.stress("wide", [
            "--accounts", 4096, "--hot", 1, "--hot-pct", 0,
            "--dup", 2, "--replay-pct", 2, "--seed", 16])


class Watchdog(unittest.TestCase):
    def test_watchdog_catches_a_real_deadlock(self):
        """Negative control. NAIVE mode locks from-then-to without
        ordering, so it must deadlock, and the watchdog must say so.
        If this test fails, a clean run of the others proves little."""
        res, _ = run(["--mode", "naive", "--threads", 8, "--ops", 2000000,
                      "--accounts", 2, "--hot", 2, "--hot-pct", 100,
                      "--watchdog", 2])
        self.assertEqual(res.returncode, 42, res.stdout + res.stderr)
        self.assertIn("deadlock", res.stderr)


if __name__ == "__main__":
    unittest.main()
