"""Crash tests for `make test`: short runs of the crash harness plus
two things kill -9 cannot show: behaviour when the disk refuses a
write, and whether fdatasync is really called.

Environment: CRASH_WORKER, LEDGER (binaries), CRASH_ITER (kills per
harness run, default 60).
"""

import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter

import crash_harness as H

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER = os.environ.get("CRASH_WORKER",
                        os.path.join(ROOT, "build", "crash_worker"))
TINY = os.path.join(ROOT, "build", "crash_worker_tiny")
TSAN = os.path.join(ROOT, "build", "crash_worker_tsan")
LEDGER = os.environ.get("LEDGER", os.path.join(ROOT, "build", "ledger"))
ITER = int(os.environ.get("CRASH_ITER", "60"))


def harness(*extra):
    res = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tests", "crash_harness.py"),
         "--worker", WORKER, "--ledger", LEDGER] + [str(a) for a in extra],
        capture_output=True, text=True)
    return res.returncode, res.stdout + res.stderr


class Kill9(unittest.TestCase):
    def run_harness(self, *extra):
        code, out = harness(*extra)
        self.assertEqual(code, 0, out)
        print("\n  " + out.strip().replace("\n", "\n  "))

    def test_full_sync(self):
        self.run_harness("--iterations", ITER, "--sync", "full",
                         "--seed", 1)

    def test_process_sync(self):
        self.run_harness("--iterations", ITER, "--sync", "process",
                         "--seed", 2)

    def test_many_threads_big_batches(self):
        self.run_harness("--iterations", ITER // 2, "--threads", 96,
                         "--accounts", 6, "--seed", 3)

    @unittest.skipUnless("CRASH_WORKER" not in os.environ, "mutant run")
    def test_tiny_log_buffer(self):
        """A 128-byte group-commit buffer holds three records, so
        appenders constantly find it full and must help flush it."""
        self.run_harness("--iterations", ITER // 2, "--worker", TINY,
                         "--seed", 4)


class Args:
    worker, ledger = WORKER, LEDGER
    threads, accounts, seed, sync = 16, 24, 9, "full"


class DiskTrouble(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ledger-io-")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_write_failure_stops_the_ledger_and_loses_nothing(self):
        """RLIMIT_FSIZE makes write() fail (possibly after a partial
        write) once the log reaches 150 kB. The worker must then get
        LG_ERROR for good (exit 4), and after reopening without the
        limit every transfer that was acknowledged must be there."""
        def limit():
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (150000, 150000))

        epoch = H.Epoch(Args, self.tmp, 0)
        res = subprocess.run(
            [WORKER, epoch.wal, epoch.dir, "16", "24", "0", "9", "full",
             "100000"], preexec_fn=limit, capture_output=True, text=True)
        self.assertEqual(res.returncode, 4, res.stderr)
        self.assertLessEqual(os.path.getsize(epoch.wal), 150000)
        stats = Counter()
        floor = epoch.read_acks(stats)
        self.assertGreater(stats["OK"], 1000)
        applied = epoch.recover_and_judge(floor, stats)
        self.assertGreaterEqual(applied, stats["OK"])

    def test_second_process_cannot_open_the_same_log(self):
        """While one process runs on a log, another must be refused,
        and must be let in once the first is dead."""
        wal = os.path.join(self.tmp, "wal")
        first = subprocess.Popen(
            [WORKER, wal, self.tmp, "4", "24", "0", "9", "process", "0"])
        try:
            deadline = time.time() + 10
            while not (os.path.exists(wal) and os.path.getsize(wal) > 8):
                self.assertLess(time.time(), deadline)
                time.sleep(0.01)
            res = subprocess.run([LEDGER, wal, "verify"],
                                 capture_output=True, text=True)
            self.assertEqual(res.returncode, 3, res.stdout)
        finally:
            first.kill()
            first.wait()
        res = subprocess.run([LEDGER, wal, "verify"], capture_output=True,
                             text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    @unittest.skipUnless(os.path.exists(TSAN), "no TSan build")
    def test_log_path_under_thread_sanitizer(self):
        """No kill here: the group-commit code runs to completion
        under ThreadSanitizer, with a 128-byte buffer so that the
        buffer-full path is taken constantly."""
        epoch = H.Epoch(Args, self.tmp, 0)
        res = subprocess.run(
            [TSAN, epoch.wal, epoch.dir, "8", "24", "0", "9", "full",
             "1500"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr[-3000:])
        self.assertNotIn("ThreadSanitizer", res.stderr)
        stats = Counter()
        floor = epoch.read_acks(stats)
        self.assertEqual(stats["returned"], 8 * 1500)
        epoch.recover_and_judge(floor, stats)

    @unittest.skipUnless(shutil.which("strace"), "strace not installed")
    def test_fdatasync_is_really_called(self):
        """kill -9 cannot tell whether fdatasync happens: the page
        cache survives the process either way. So count the system
        calls and compare them with the number of write batches the
        ledger itself reports. FULL: exactly one fdatasync per batch.
        PROCESS: batches, but no fdatasync. How many batches there are
        depends on the machine (a faster disk means smaller batches),
        so no absolute number is expected."""
        counts = {}
        batches = {}
        for sync in ("full", "process"):
            wal = os.path.join(self.tmp, "wal." + sync)
            res = subprocess.run(
                ["strace", "-f", "-c", "-e", "trace=fdatasync,fsync",
                 WORKER, wal, self.tmp, "8", "24", "0", "9", sync, "500"],
                capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr[-500:])
            # strace -c line: % time, seconds, usecs/call, calls,
            # [errors], syscall. The call count is the fourth column.
            counts[sync] = 0
            for line in res.stderr.split("\n"):
                cols = line.split()
                if len(cols) >= 5 and cols[-1] == "fdatasync":
                    counts[sync] = int(cols[3])
            batches[sync] = int(re.search(r"flushes=(\d+)",
                                          res.stdout).group(1))
        self.assertGreater(batches["full"], 0)
        self.assertGreater(batches["process"], 0)
        self.assertEqual(counts["full"], batches["full"])
        self.assertEqual(counts["process"], 0)


if __name__ == "__main__":
    unittest.main()
