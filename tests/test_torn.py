"""Torn tail or corruption? The boundary cases of recovery (gap 2).

The question for every damaged log is the one invariant I5 asks: could
an acknowledged transfer be lost by cutting here? A batch is
acknowledged after its fdatasync returned, and the next batch is only
written after that. So:

  cut      the damage can be explained as the ONE batch that was being
           written when the machine died (its blocks may have reached
           the disk in any order, or not at all)
  refuse   anything proves a later write behind the damage, or the
           unreadable tail is larger than a batch can be. The file
           must be left byte-for-byte untouched and the reason named.

Each expectation below is derived from the batch layout by hand, and
additionally compared with walfmt.verdict (an independent Python
implementation of the rule).

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
ENV = dict(os.environ, LEDGER_SYNC="process")
MIB = 1 << 20

# Four batches of different sizes, as group commit writes them.
BATCHES = [
    [walfmt.create(0, 0, 1), walfmt.create(1, 1000), walfmt.create(2, 0)],
    [walfmt.transfer(10, 0, 1, 500)],
    [walfmt.transfer(11, 1, 2, 1500), walfmt.transfer(12, 2, 1, 7)],
    [walfmt.transfer(13, 0, 2, 2**62), walfmt.transfer(14, 2, 0, 2**62 - 1),
     walfmt.create(3, 5), walfmt.transfer(15, 3, 1, 5)],
]
FULL = walfmt.log(BATCHES)
ENDS = [len(walfmt.log(BATCHES[:n])) for n in range(len(BATCHES) + 1)]
LAST = ENDS[-2]         # offset where the last batch starts


def state_of(data):
    """Model state of the complete batches in `data`."""
    m = M.Model(1 << 16)
    for rec in walfmt.parse(data)[0]:
        if rec[0] == walfmt.CREATE:
            assert m.create(rec[2], rec[3]) == (M.OK, rec[1])
        else:
            assert m.transfer(*rec[1:]) == M.OK
    return m


class TornOrCorrupt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "wal")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        res = subprocess.run([LEDGER, self.path] + [str(a) for a in args],
                             capture_output=True, text=True, env=ENV)
        return res.returncode, res.stdout.strip(), res.stderr.strip()

    def put(self, data):
        with open(self.path, "wb") as f:
            f.write(data)

    def get(self):
        with open(self.path, "rb") as f:
            return f.read()

    def cut(self, data, keep, note):
        """Recovery must accept `data`, keep exactly `keep` (a prefix
        of complete batches), cut the rest, and be repeatable."""
        self.assertIsNone(walfmt.verdict(data), note)
        self.put(data)
        code, out, err = self.run_cli("dump")
        self.assertEqual(code, 0, note + ": " + err)
        self.assertEqual(self.get(), keep, note)
        m = state_of(keep)
        r, d = [line.split() for line in out.split("\n")]
        cells = [c.split(":") for c in d[4:]]
        self.assertEqual([int(c[0]) for c in cells], m.balances, note)
        self.assertEqual(int(r[2]), len(data) - len(keep), note)
        code, again, _ = self.run_cli("dump")
        self.assertEqual(again.split("\n")[1], out.split("\n")[1], note)
        self.assertEqual(int(again.split()[2]), 0, note)
        check_all(Observation(
            balances=m.balances, flags=[int(c[1]) for c in cells],
            initials=m.initials, applied=m.log, ordered=True,
            recovery_a=out.split("\n")[1], recovery_b=again.split("\n")[1]))

    def refuse(self, data, note, why=None, rule=True):
        """Recovery must refuse `data`, say why, and not touch it."""
        if rule:
            self.assertIsNotNone(walfmt.verdict(data), note)
        self.put(data)
        for cmd in (["verify"], ["dump"], ["balance", 1],
                    ["transfer", 999, 0, 1, 1]):
            code, out, err = self.run_cli(*cmd)
            self.assertEqual(code, 3, "%s: %s" % (note, cmd))
            self.assertIn("CORRUPT", err, note)
            self.assertIn("file left untouched", err, note)
            if why:
                self.assertIn(why, err, note)
            self.assertEqual(self.get(), data, note + ": file modified")

    # -- one damaged byte, every position ------------------------------

    def test_every_byte_damaged(self):
        """In the last batch: cut that batch. In any earlier batch:
        refuse, because later batches prove it was acknowledged."""
        for pos in range(8, len(FULL)):
            for mask in (0x01, 0x10, 0xFF):
                bad = bytearray(FULL)
                bad[pos] ^= mask
                note = "byte %d ^ %#x" % (pos, mask)
                if pos >= LAST:
                    self.cut(bytes(bad), FULL[:LAST], note)
                else:
                    self.refuse(bytes(bad), note, "written later")

    def test_bad_checksum_on_a_valid_record_in_the_middle(self):
        """The record's bytes are all there; only its stored CRC is
        wrong. Middle of the log: refuse. This is the case the old
        1 MiB rule got wrong: it cut here and silently dropped every
        acknowledged batch behind it."""
        off = ENDS[1]               # the single transfer of batch 2
        for crc_byte in range(4, 8):
            bad = bytearray(FULL)
            bad[off + crc_byte] ^= 0x55
            self.refuse(bytes(bad), "crc byte %d" % crc_byte,
                        "acknowledged transfers would be lost")
        bad = bytearray(FULL)
        bad[LAST + 4] ^= 0x55       # same thing in the last batch
        self.cut(bytes(bad), FULL[:LAST], "crc of the last batch")

    def test_several_damaged_bytes(self):
        """Two to four damaged bytes anywhere. Let k be the first
        damaged batch. Refuse if any commit record from batch k on is
        still intact while k is not the last batch: it proves a write
        after batch k began. (If every one of them is destroyed, see
        the known limit below.)"""
        rng = random.Random(11)
        seen = set()
        for _ in range(400):
            bad = bytearray(FULL)
            where = [rng.randrange(8, len(FULL))
                     for _ in range(rng.randrange(2, 5))]
            for pos in where:
                bad[pos] ^= rng.randrange(1, 256)
            if bytes(bad) == FULL:
                continue
            hit = [any(ENDS[i + 1] - 17 <= pos < ENDS[i + 1]
                       and bad[pos] != FULL[pos] for pos in where)
                   for i in range(len(BATCHES))]
            k = max(i for i in range(len(BATCHES)) if ENDS[i] <= min(where))
            if k == len(BATCHES) - 1:
                seen.add("cut")
                self.cut(bytes(bad), FULL[:LAST], str(where))
            elif not all(hit[k:]):
                seen.add("refuse")
                self.refuse(bytes(bad), str(where))
            else:
                seen.add("limit")
                self.cut(bytes(bad), FULL[:ENDS[k]], str(where))
        self.assertEqual(seen, {"cut", "refuse", "limit"})

    def test_known_limit_every_later_commit_record_destroyed(self):
        """NOT a guarantee, a documented limit (found by the test
        above). Batch 3 was acknowledged (batch 4 was written after
        it). Damage destroys the commit record of batch 3 AND the
        commit record of batch 4. No commit record behind the damage
        is left, so what remains looks exactly like one interrupted
        batch with two holes, and recovery cuts it: the transfers of
        batch 3 are lost without an error. It takes two separate
        damaged places inside the last 1 MiB. Closing it needs a
        batch tag in every data record (DESIGN, 'What is not
        verified')."""
        bad = bytearray(FULL)
        bad[ENDS[3] - 5] ^= 1
        bad[ENDS[4] - 5] ^= 1
        self.cut(bytes(bad), FULL[:ENDS[2]], "both commit records hit")
        # One of the two alone is caught.
        bad[ENDS[4] - 5] ^= 1
        self.refuse(bytes(bad), "only the commit record of batch 3 hit")

    # -- blocks that reached the disk out of order ---------------------

    def test_hole_in_the_last_batch(self):
        """The batch in flight: any window of it missing (zeros or old
        rubbish), with the bytes after the hole present, including the
        batch's own intact commit record. Never acknowledged: cut."""
        rng = random.Random(5)
        size = len(FULL) - LAST
        for start in range(size):
            for width in (1, 8, 17, 41, 64, size):
                for fill in (b"\0", None):
                    bad = bytearray(FULL)
                    for i in range(LAST + start,
                                   min(LAST + start + width, len(FULL))):
                        bad[i] = 0 if fill else rng.randrange(256)
                    if bytes(bad) == FULL:
                        continue
                    self.cut(bytes(bad), FULL[:LAST],
                             "hole at %d width %d" % (start, width))

    def test_earlier_batch_missing_later_batch_present(self):
        """A whole earlier batch zero-filled or missing while a later
        one is intact. That cannot be one interrupted write: refuse."""
        for k in range(len(BATCHES) - 1):
            a, b = ENDS[k], ENDS[k + 1]
            zeroed = FULL[:a] + b"\0" * (b - a) + FULL[b:]
            self.refuse(zeroed, "batch %d zero-filled" % k, "written later")
            half = FULL[:a] + b"\0" * ((b - a) // 2) + FULL[a + (b - a) // 2:]
            self.refuse(half, "first half of batch %d zero-filled" % k)
            self.refuse(FULL[:a] + FULL[b:], "batch %d removed" % k)
        self.refuse(FULL[:ENDS[1]] + FULL[ENDS[2]:ENDS[3]] + FULL[ENDS[1]:
                    ENDS[2]] + FULL[ENDS[3]:], "batches 2 and 3 swapped")

    def test_bytes_behind_the_commit_record_of_a_damaged_batch(self):
        """The last batch is damaged but its commit record is intact
        and something follows it. Whatever follows was written later,
        so the damaged batch had been acknowledged: refuse."""
        bad = bytearray(FULL)
        bad[LAST + 10] ^= 1
        for tail in (b"\0", b"\0" * 41, walfmt.transfer(77, 0, 1, 1),
                     walfmt.transfer(77, 0, 1, 1)[:20]):
            self.refuse(bytes(bad) + tail, "tail %r" % tail[:4],
                        "written later")
        # The same tails behind an undamaged log are just a torn batch.
        for tail in (b"\0", b"\0" * 41, walfmt.transfer(77, 0, 1, 1)):
            self.cut(FULL + tail, FULL, "clean log + %r" % tail[:4])

    # -- stale but intact records after the true tail -------------------

    def test_stale_records_without_a_commit_record_are_cut(self):
        """Old, perfectly intact records after the true end, with no
        commit record: nobody was ever told they happened."""
        stale = [BATCHES[1][0], walfmt.transfer(50, 0, 1, 9),
                 walfmt.create(4, 100), b"".join(BATCHES[3])]
        for i, tail in enumerate(stale):
            self.cut(FULL + tail, FULL, "stale tail %d" % i)
            self.cut(FULL + tail + b"\0" * 100, FULL, "stale tail %d+0" % i)
            self.cut(FULL[:LAST] + tail, FULL[:LAST], "stale at LAST %d" % i)

    def test_stale_batch_from_another_offset_is_refused(self):
        """A complete old batch (records and commit record) that
        belongs to a different position in the file."""
        for k in range(len(BATCHES)):
            old = FULL[ENDS[k]:ENDS[k + 1]]
            self.refuse(FULL + old, "copy of batch %d appended" % k)
            self.refuse(FULL + old + b"\0" * 9, "copy of batch %d + 0" % k)
        self.refuse(FULL + walfmt.commit(8), "lone commit record, start 8")
        self.refuse(FULL + walfmt.commit(len(FULL) + 41),
                    "commit record pointing forward")
        self.refuse(FULL + b"\0" * 50 + walfmt.commit(8) + b"\0" * 50,
                    "foreign commit record inside rubbish")

    def test_stale_batch_at_the_right_offset_breaking_the_rules(self):
        """Complete, at the right offset, but replaying it is
        impossible (id already used): refused by the rules check."""
        self.refuse(walfmt.seal(FULL, [BATCHES[1][0]]), "repeated id",
                    "breaks the ledger's rules", rule=False)

    def test_known_limit_stale_batch_at_the_right_offset(self):
        """NOT a guarantee, a documented limit (DESIGN, 'What is not
        verified'): a complete batch that names exactly this offset
        and that the rules accept is believed. A batch that recovery
        once cut off, and that a misbehaving disk brought back
        unchanged, would look like this. Telling it apart needs a
        generation number in the commit record."""
        ghost = walfmt.seal(FULL, [walfmt.transfer(60, 0, 1, 1)])
        self.cut(ghost, ghost, "believed")

    # -- exactly at, just inside, just beyond the 1 MiB limit -----------

    def big_batch(self, start, size):
        """A legal batch of exactly `size` bytes starting at `start`:
        transfers 0 -> 1 with fresh ids, padded with creates."""
        for creates in range(41):
            rest = size - walfmt.COMMIT_SIZE - 29 * creates
            if rest >= 0 and rest % 41 == 0:
                break
        recs = [walfmt.transfer(1000 + i, 0, 1, 1) for i in range(rest // 41)]
        recs += [walfmt.create(4 + i, 0) for i in range(creates)]
        data = b"".join(recs) + walfmt.commit(start)
        assert len(data) == size
        return data

    def test_unreadable_tail_at_the_limit(self):
        rng = random.Random(8)
        junk = bytes(rng.randrange(256) for _ in range(4096)) * 257
        for fill in (b"\0" * (MIB + 1), junk):
            self.cut(FULL + fill[:MIB - 1], FULL, "limit - 1")
            self.cut(FULL + fill[:MIB], FULL, "exactly the limit")
            self.refuse(FULL + fill[:MIB + 1], "limit + 1",
                        "one interrupted write")

    def test_largest_possible_batch(self):
        """A batch that fills the whole commit buffer (1 MiB)."""
        big = self.big_batch(len(FULL), MIB)
        whole = FULL + big
        self.cut(whole, whole, "complete 1 MiB batch")
        for pos in (0, 1, 40, MIB // 2, MIB - 18, MIB - 17, MIB - 1):
            bad = bytearray(whole)
            bad[len(FULL) + pos] ^= 0x04
            self.cut(bytes(bad), FULL, "1 MiB batch damaged at %d" % pos)
        # Its first block missing, everything else on disk.
        self.cut(FULL + b"\0" * 4096 + big[4096:], FULL, "first block lost")
        # Its last block missing: only the commit record is gone.
        self.cut(FULL + big[:-4096] + b"\0" * 4096, FULL, "last block lost")
        self.cut(FULL + big[:-4096], FULL, "last block never allocated")

    def test_damage_just_beyond_the_limit(self):
        """Damage followed by more than one batch can hold."""
        big = self.big_batch(len(FULL), MIB)
        # Last byte of the batch before the 1 MiB batch: 1 MiB + 1
        # unreadable bytes, and a later batch behind the damage.
        bad = bytearray(FULL + big)
        bad[len(FULL) - 1] ^= 1
        self.refuse(bytes(bad), "damage 1 byte before a 1 MiB batch")
        # A full batch, then one more small batch, damage in the first.
        more = walfmt.seal(FULL + big, [walfmt.transfer(5, 0, 1, 1)])
        for pos in (0, MIB // 2, MIB - 1):
            bad = bytearray(more)
            bad[len(FULL) + pos] ^= 1
            self.refuse(bytes(bad), "1 MiB batch damaged at %d, one "
                        "batch behind it" % pos)
        # Damage in the small last batch: an ordinary torn tail.
        bad = bytearray(more)
        bad[-20] ^= 1
        self.cut(bytes(bad), FULL + big, "small batch after a 1 MiB one")

    # -- end to end, through the CLI ------------------------------------

    def test_acknowledged_transfers_are_never_dropped_silently(self):
        """The defect this file was written for, as a user would hit
        it: four acknowledged transfers, one flipped bit in the second.
        Before D25: 'OK: ... 3 log records replayed' and balance 100.
        Now: refused; and once the byte is restored, all 400 are back."""
        self.run_cli("create", 0, "overdraft")
        self.run_cli("create", 0)
        for tid in (1, 2, 3, 4):
            self.assertEqual(self.run_cli("transfer", tid, 0, 1, 100)[:2],
                             (0, "OK"))
        good = self.get()
        self.assertEqual(walfmt.parse(good)[1], len(good))
        second = good.index(walfmt.transfer(2, 0, 1, 100))
        bad = bytearray(good)
        bad[second + 20] ^= 1
        self.refuse(bytes(bad), "bit flip in transfer 2",
                    "acknowledged transfers would be lost")
        self.put(good)
        self.assertEqual(self.run_cli("balance", 1)[:2], (0, "OK 400"))


if __name__ == "__main__":
    unittest.main()
