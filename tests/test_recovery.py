"""Deterministic recovery tests: damaged logs built by hand.

The crash harness (crash_harness.py) attacks recovery at random; this
file does it exhaustively on a small log: cut at every byte, flip
every byte, append rubbish, and plant records that are intact but
impossible. Every recovered state goes to the shared checker.

Environment: LEDGER (default build/ledger).
"""

import os
import random
import subprocess
import tempfile
import unittest

import model as M
import walfmt
from invariants import Observation, check_all

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.environ.get("LEDGER", os.path.join(ROOT, "build", "ledger"))
ENV = dict(os.environ, LEDGER_SYNC="process")   # speed; same code path


def cli(path, *args):
    res = subprocess.run([LEDGER, path] + [str(a) for a in args],
                         capture_output=True, text=True, env=ENV)
    return res.returncode, res.stdout.strip()


def dump(path):
    """Returns None if the ledger refuses to open, else a dict."""
    code, out = cli(path, "dump")
    if code != 0:
        return None
    r, d = [line.split() for line in out.split("\n")]
    cells = [c.split(":") for c in d[4:]]
    return {"recovered": int(r[1]), "truncated": int(r[2]),
            "count": int(d[1]), "seeded": int(d[2]), "verify": d[3],
            "balances": [int(c[0]) for c in cells],
            "flags": [int(c[1]) for c in cells], "raw": out}


def replay(records):
    """What the model says a list of log records amounts to."""
    m = M.Model(1 << 16)
    for rec in records:
        if rec[0] == walfmt.CREATE:
            rc, acc = m.create(rec[2], rec[3])
            assert rc == M.OK and acc == rec[1], rec
        else:
            assert m.transfer(*rec[1:]) == M.OK, rec
    return m


SAMPLE = [
    walfmt.create(0, 0, 1),
    walfmt.create(1, 1000),
    walfmt.create(2, 0),
    walfmt.transfer(10, 0, 1, 500),
    walfmt.transfer(11, 1, 2, 1500),
    walfmt.transfer(2**64 - 1, 2, 1, 7),
    walfmt.transfer(13, 0, 2, 2**62),
    walfmt.transfer(14, 2, 0, 2**62 - 1),
    walfmt.create(3, 5),
    walfmt.transfer(15, 3, 1, 5),
]


class Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "wal")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        with open(self.path, "wb") as f:
            f.write(data)

    def read(self):
        with open(self.path, "rb") as f:
            return f.read()

    def expect_prefix(self, data, nrec, note):
        """`data` is on disk; recovery must yield exactly the first
        nrec SAMPLE records, cut the file there, and be repeatable."""
        self.write(data)
        first = dump(self.path)
        self.assertIsNotNone(first, note)
        good = walfmt.MAGIC + b"".join(SAMPLE[:nrec])
        self.assertEqual(self.read(), good, note)
        m = replay(walfmt.parse(good)[0])
        second = dump(self.path)
        self.assertEqual(first["balances"], m.balances, note)
        self.assertEqual(first["recovered"], nrec, note)
        self.assertEqual(first["truncated"], len(data) - min(len(data),
                         len(good)) if len(data) >= 8 else len(data), note)
        self.assertEqual(second["truncated"], 0, note)
        check_all(Observation(
            balances=first["balances"], flags=first["flags"],
            initials=m.initials, applied=m.log, ordered=True,
            recovery_a=first["raw"].split("\n")[1],
            recovery_b=second["raw"].split("\n")[1]))

    def expect_refused(self, data, note):
        self.write(data)
        code, _ = cli(self.path, "verify")
        self.assertEqual(code, 3, note)
        self.assertEqual(self.read(), data, note + ": file was modified")

    def test_log_written_by_python_is_read_by_c(self):
        self.expect_prefix(walfmt.MAGIC + b"".join(SAMPLE), len(SAMPLE),
                           "python-written log")

    def test_log_written_by_c_is_read_by_python(self):
        cli(self.path, "create", 0, "overdraft")
        cli(self.path, "create", 1000)
        cli(self.path, "transfer", 10, 0, 1, 500)
        cli(self.path, "transfer", 2**64 - 1, 1, 0, 1)
        data = self.read()
        recs, end = walfmt.parse(data)
        self.assertEqual(end, len(data))
        self.assertEqual(recs, [(1, 0, 0, 1), (1, 1, 1000, 0),
                                (2, 10, 0, 1, 500),
                                (2, 2**64 - 1, 1, 0, 1)])
        self.assertEqual(data, walfmt.MAGIC + walfmt.create(0, 0, 1)
                         + walfmt.create(1, 1000)
                         + walfmt.transfer(10, 0, 1, 500)
                         + walfmt.transfer(2**64 - 1, 1, 0, 1))

    def test_torn_at_every_byte(self):
        full = walfmt.MAGIC + b"".join(SAMPLE)
        bounds = [len(walfmt.MAGIC)]
        for rec in SAMPLE:
            bounds.append(bounds[-1] + len(rec))
        for cut in range(len(full) + 1):
            whole = sum(1 for b in bounds[1:] if b <= cut)
            self.expect_prefix(full[:cut], whole, "cut at byte %d" % cut)

    def test_every_byte_corrupted(self):
        """One damaged byte anywhere: recovery keeps exactly the
        records before the damaged one."""
        full = walfmt.MAGIC + b"".join(SAMPLE)
        bounds = [len(walfmt.MAGIC)]
        for rec in SAMPLE:
            bounds.append(bounds[-1] + len(rec))
        for pos in range(len(walfmt.MAGIC), len(full)):
            for mask in (0x01, 0x80, 0xFF):
                bad = bytearray(full)
                bad[pos] ^= mask
                whole = sum(1 for b in bounds[1:] if b <= pos)
                self.expect_prefix(bytes(bad), whole,
                                   "byte %d ^ %#x" % (pos, mask))

    def test_rubbish_after_the_last_record(self):
        full = walfmt.MAGIC + b"".join(SAMPLE)
        rng = random.Random(3)
        tails = [b"\0" * n for n in (1, 7, 8, 9, 41, 4096)]
        tails += [bytes(rng.randrange(256) for _ in range(n))
                  for n in (1, 8, 40, 41, 42, 500, 100000)]
        tails.append(SAMPLE[3])                  # replayed old record
        tails.append(walfmt.transfer(99, 1, 2, 10)[:-1])
        tails.append(b"\0" * 2**20)              # exactly the limit
        for i, tail in enumerate(tails):
            if tail is SAMPLE[3]:
                continue
            self.expect_prefix(full + tail, len(SAMPLE), "tail %d" % i)

    def test_more_damage_than_a_torn_write_can_cause_is_refused(self):
        full = walfmt.MAGIC + b"".join(SAMPLE)
        self.expect_refused(full + b"\0" * (2**20 + 1), "1 MiB + 1 tail")
        bad = bytearray(full + walfmt.transfer(50, 0, 1, 1) * 30000)
        bad[len(full) + 3] ^= 0x10
        self.expect_refused(bytes(bad), "damage with 1.2 MB behind it")

    def test_intact_but_impossible_records_are_refused(self):
        full = walfmt.MAGIC + b"".join(SAMPLE)
        cases = {
            "transfer id repeated": SAMPLE[3],
            "overdraw": walfmt.transfer(50, 1, 2, 10**9),
            "unknown account": walfmt.transfer(50, 1, 9, 1),
            "zero amount": walfmt.transfer(50, 1, 2, 0),
            "negative amount": walfmt.transfer(50, 1, 2, -5),
            "self transfer": walfmt.transfer(50, 1, 1, 5),
            "credit overflow": walfmt.transfer(50, 0, 1, 2**63 - 1),
            "create with wrong id": walfmt.create(7, 0),
            "create with negative balance": walfmt.create(4, -1),
            "create with unknown flag": walfmt.create(4, 0, 2),
            "seeded total overflow": walfmt.create(4, 2**63 - 1),
        }
        for note, rec in cases.items():
            self.expect_refused(full + rec, note)
            self.expect_refused(full + rec + SAMPLE[3], note + " + more")

    def test_wrong_file_is_refused_and_not_touched(self):
        self.expect_refused(b"LGWAL002", "future version")
        self.expect_refused(b"hello", "short foreign file")
        self.expect_refused(b"\0" * 9, "zeros longer than a header")
        self.expect_refused(b"LGWAL00\0", "magic with a wrong byte")
        self.expect_refused(b"\0GWAL001" + SAMPLE[0], "damaged magic")
        self.expect_refused(b"#!/bin/sh\necho important\n", "a script")

    def test_torn_header_counts_as_a_new_ledger(self):
        """A crash while the file is being created can leave it
        empty, with part of the magic, or with its size set but the
        data never written (zeros). Nothing was ever acknowledged."""
        torn = [walfmt.MAGIC[:cut] for cut in range(len(walfmt.MAGIC))]
        torn += [b"\0" * n for n in range(1, len(walfmt.MAGIC) + 1)]
        for data in torn:
            self.write(data)
            got = dump(self.path)
            self.assertEqual(got["count"], 0, data)
            self.assertEqual(self.read(), walfmt.MAGIC, data)
        self.assertEqual(cli(self.path, "create", 5), (0, "OK 0"))

    def test_idempotency_survives_restart(self):
        cli(self.path, "create", 0, "overdraft")
        cli(self.path, "create", 0)
        self.assertEqual(cli(self.path, "transfer", 7, 0, 1, 40), (0, "OK"))
        self.assertEqual(cli(self.path, "transfer", 7, 0, 1, 40),
                         (0, "DUPLICATE"))
        self.assertEqual(cli(self.path, "transfer", 7, 0, 1, 41),
                         (1, "INVALID"))
        self.assertEqual(cli(self.path, "transfer", 8, 1, 0, 41),
                         (1, "INSUFFICIENT_FUNDS"))
        self.assertEqual(cli(self.path, "balance", 1), (0, "OK 40"))
        self.assertEqual(len(walfmt.parse(self.read())[0]), 3)

    def test_cli_rejects_malformed_numbers(self):
        cli(self.path, "create", 100)
        cli(self.path, "create", 0)
        before = self.read()
        for amount in ("1.5", "1e3", "", " 5", "5 ", "0x10",
                       "9223372036854775808", "+5", "five"):
            code, _ = cli(self.path, "transfer", 1, 0, 1, amount)
            self.assertEqual(code, 2, repr(amount))
        for acc in ("-1", "18446744073709551616", "1.0"):
            code, _ = cli(self.path, "transfer", 1, acc, 1, 5)
            self.assertEqual(code, 2, repr(acc))
        self.assertEqual(self.read(), before)


if __name__ == "__main__":
    unittest.main()
