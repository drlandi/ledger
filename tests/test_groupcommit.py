"""Group commit under rare interleavings, and reads vs. durability
(gaps 1 and 3).

Two kinds of test:

  gate    build/gate holds fdatasync shut and releases one batch at a
          time (tests/gate.c). Deterministic: who may have returned at
          each step is known exactly.
  storm   a 128-byte commit buffer (two records per batch), many
          threads on three accounts, so nearly every appender finds the
          buffer full while holding its account locks. Run to
          completion under ThreadSanitizer, and under kill -9. Every
          transfer answer and every balance / lookup / snapshot the
          workers were shown is checked against the durable log
          (crash_harness.explain).

Environment: GATE, CRASH_WORKER_TINY, LEDGER (binaries; set by the
mutation check), CRASH_ITER (kills, default 60).
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter

import crash_harness as H
import walfmt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
B = os.path.join(ROOT, "build")
MUTANT = "GATE" in os.environ
GATE = os.environ.get("GATE", os.path.join(B, "gate"))
GATE_TSAN = os.path.join(B, "gate_tsan")
TINY = os.environ.get("CRASH_WORKER_TINY",
                      os.path.join(B, "crash_worker_tiny"))
TINY_TSAN = os.path.join(B, "crash_worker_tsan")
LEDGER = os.environ.get("LEDGER", os.path.join(B, "ledger"))
ITER = int(os.environ.get("CRASH_ITER", "60"))


def batches(data):
    """The log as a list of batches, each a list of transfer ids
    ('c' for a create record)."""
    out, cur, off = [], [], 8
    while off < len(data):
        rec, size = walfmt.one(data, off)
        off += size
        if rec[0] == walfmt.COMMIT:
            out.append(cur)
            cur = []
        else:
            cur.append(rec[1] if rec[0] == walfmt.XFER else "c")
    assert not cur
    return out


class Gate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ledger-gate-")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_gate(self, exe, n):
        wal = os.path.join(self.tmp, "wal%d" % n)
        res = subprocess.run([exe, wal], capture_output=True, text=True,
                             timeout=30 if MUTANT else 150)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr[-3000:])
        self.assertNotIn("Sanitizer", res.stderr)
        self.assertIn("PASS", res.stdout)
        return wal, res.stdout

    def test_nothing_returns_before_its_batch_is_durable(self):
        """Five runs; in each, every step of tests/gate.c must hold,
        and the log must show the batches exactly as they were forced:
        [A] alone, then [B C] (the full buffer), then D, with E (which
        spends D's money) after D, and nothing lost or doubled."""
        for n in range(5):
            wal, out = self.run_gate(GATE, n)
            with open(wal, "rb") as f:
                data = f.read()
            self.assertEqual(walfmt.parse(data)[1], len(data))
            got = batches(data)
            ids = [i for b in got for i in b if i != "c"]
            self.assertEqual(sorted(ids), [1, 3, 4, 10, 11, 12, 13, 14])
            self.assertIn([1], got)
            self.assertIn([3], got)
            self.assertIn([4], got)
            self.assertIn([10], got)
            self.assertIn([11, 12], got)
            self.assertLess(ids.index(13), ids.index(14))
            self.assertTrue(all(len(b) <= 2 for b in got), got)
        print("\n  " + out.strip().replace("\n", "\n  "))

    @unittest.skipIf(MUTANT or not os.path.exists(GATE_TSAN), "no TSan")
    def test_same_under_thread_sanitizer(self):
        for n in range(3):
            self.run_gate(GATE_TSAN, n)


class Args:
    ledger = LEDGER
    threads, accounts, seed, sync = 32, 3, 21, "full"


class Storm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ledger-storm-")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def complete_run(self, exe, threads, ops, sync):
        """No kill: the run finishes, then the log and every answer
        are judged exactly as after a crash."""
        epoch = H.Epoch(Args, self.tmp, 0)
        res = subprocess.run(
            [exe, epoch.wal, epoch.dir, str(threads), "3", "0", "21",
             sync, str(ops)], capture_output=True, text=True,
            timeout=30 if MUTANT else 600)
        self.assertEqual(res.returncode, 0, res.stderr[-3000:])
        self.assertNotIn("Sanitizer", res.stderr)
        flushes, full = map(int, re.search(
            r"flushes=(\d+) full_waits=(\d+)", res.stdout).groups())
        stats = Counter()
        floor = epoch.read_acks(stats)
        self.assertEqual(stats["returned"], threads * ops)
        applied = epoch.recover_and_judge(floor, stats)
        self.assertEqual(applied, stats["OK"])
        self.assertGreater(stats["read_balance"], threads * ops // 8)
        self.assertGreater(stats["read_lookup"], threads * ops // 16)
        self.assertGreater(stats["read_snapshot"], threads * ops // 128)
        with open(epoch.wal, "rb") as f:
            got = batches(f.read())
        self.assertTrue(all(len(b) <= 2 for b in got))
        return flushes, full, stats

    def test_buffer_full_storm_runs_to_completion(self):
        flushes, full, stats = self.complete_run(TINY, 32, 1500, "full")
        # The path under test must really be the common case.
        self.assertGreater(full, stats["OK"] // 4, (flushes, full))
        print("\n  storm: %d transfers applied, %d batches, buffer found "
              "full %d times; reads checked: balance=%d lookup=%d "
              "snapshot=%d" % (stats["OK"], flushes, full,
                               stats["read_balance"], stats["read_lookup"],
                               stats["read_snapshot"]))

    def test_buffer_full_storm_process_sync(self):
        flushes, full, stats = self.complete_run(TINY, 32, 1500, "process")
        self.assertGreater(full, 0)

    @unittest.skipIf(MUTANT or not os.path.exists(TINY_TSAN), "no TSan")
    def test_buffer_full_storm_under_thread_sanitizer(self):
        flushes, full, stats = self.complete_run(TINY_TSAN, 32, 400, "full")
        self.assertGreater(full, stats["OK"] // 4, (flushes, full))
        print("\n  TSan storm: %d transfers, buffer found full %d times, "
              "no report" % (stats["OK"], full))

    def harness(self, *extra):
        res = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tests", "crash_harness.py"),
             "--ledger", LEDGER, "--accounts", "3"]
            + [str(a) for a in extra], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        print("\n  " + res.stdout.strip().replace("\n", "\n  "))

    def test_buffer_full_storm_under_kill9(self):
        self.harness("--worker", TINY, "--threads", 64, "--iterations",
                     ITER, "--seed", 31)

    def test_buffer_full_storm_under_kill9_process_sync(self):
        self.harness("--worker", TINY, "--threads", 64, "--iterations",
                     ITER // 2, "--seed", 32, "--sync", "process")

    @unittest.skipIf(MUTANT or not os.path.exists(TINY_TSAN), "no TSan")
    def test_buffer_full_storm_under_kill9_and_thread_sanitizer(self):
        """The harness fails if a killed worker had printed a
        sanitizer report."""
        self.harness("--worker", TINY_TSAN, "--threads", 32,
                     "--iterations", ITER // 3, "--seed", 33,
                     "--min-ms", 40, "--max-ms", 400)


if __name__ == "__main__":
    unittest.main()
