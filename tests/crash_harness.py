"""Crash harness: run the workload, kill -9 it, damage the unsynced
tail of the log, recover, and hold the result to I1-I6.

One iteration:
  1. start build/crash_worker on the ledger left by the last victim
  2. after a random delay, SIGKILL it (sometimes within the first
     milliseconds, i.e. during recovery or account creation)
  3. read the acks: every transfer call that returned, with the log
     offset the ledger had declared durable at that time
  4. tear the tail: everything past the highest durable offset any ack
     mentions was never promised, so the harness may truncate it at a
     random byte, flip bytes in it, zero a block of it, or append
     rubbish after it. This imitates what a power cut may do to
     unsynced data. If the damage lands in front of a batch that was
     written later, it is not something one interrupted write can
     cause: recovery must then REFUSE the log and leave it untouched
     (walfmt.verdict decides, independently of the C code). The
     harness checks that, puts the undamaged bytes back, and goes on.
  5. recover twice independently (a copy and the original) and once
     more on the already recovered file
  6. parse the recovered log with the Python reader (walfmt.py) and
     give everything to the shared checker:
       I1 I2 I4  recovered balances vs. whole log records, I2 at every
                 prefix of the log
       I3        no transfer id twice in the log
       I5        every OK / DUPLICATE ack of this ledger's whole life
                 is in the log with the same parameters
       I6        the three recoveries agree
     plus: C and Python agree on where the valid log ends, and
     recovery never cuts below a durable offset.
  7. reads (gap 3): every balance, lookup, snapshot and rejection a
     worker was shown must be explainable by the recovered log up to
     the durable offset reported right after that call (explain()).

Every `--epoch` iterations a fresh ledger is started so the log (and
the time to re-check its whole history) stays bounded.

Usage: python3 tests/crash_harness.py [--iterations N] [--sync full]
Exit status 0 only if no invariant was violated. On failure the
ledger directory is kept and its path printed.
"""

import argparse
import os
import random
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import walfmt                                            # noqa: E402
from invariants import Observation, check_all            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACK = struct.Struct("<QQQqQII")
RC_OK, RC_DUPLICATE, RC_INSUFFICIENT = 0, 1, 2
K_XFER, K_BALANCE, K_LOOKUP, K_SNAPSHOT = 0, 1, 2, 3
INF = float("inf")
RC_NAMES = ["OK", "DUPLICATE", "INSUFFICIENT_FUNDS", "UNKNOWN_ACCOUNT",
            "INVALID", "FULL", "ERROR", "CORRUPT"]


class Failure(Exception):
    pass


def explain(items, events, snaps, stats):
    """Gap 3: nothing a caller was shown may come from state that was
    not durable yet. `items` is the recovered log as (record, offset
    just past it); `events` are one iteration's acks per thread, in
    order, as (kind, tid, src, dst, amount, durable, rc); `snaps` are
    (durable, [balances]). `durable` is what the ledger reported right
    after the call returned, so everything the call showed must
    already be in the log at or before that offset:

      OK / DUPLICATE        the transfer's record ends at or before it
      INSUFFICIENT_FUNDS    at some point up to it the source held
                            less than the amount
      balance               the account had exactly that balance at
                            some point up to it, and not earlier than
                            the caller's own last acknowledged
                            transfer on that account
      lookup found          as OK; lookup "not found" after the same
                            thread was told OK is a lie
      snapshot              the whole vector equals the state after
                            some prefix of the log up to it
    """
    tid_at, tid_rec = {}, {}
    offs, bals, lows, by_bal, flags = [], [], [], {}, []
    state, key = [], 0
    want = {}
    for dur, vec in snaps:
        k = 0
        for acc, bal in enumerate(vec):
            k ^= hash((acc, bal))
        want.setdefault(k, []).append([dur, vec, False])

    def moved(acc, bal, off):
        by_bal.setdefault((acc, bal), []).append(len(offs[acc]))
        low = min(bal, lows[acc][-1]) if lows[acc] else bal
        offs[acc].append(off)
        bals[acc].append(bal)
        lows[acc].append(low)

    for rec, off in items:
        if rec[0] == walfmt.CREATE:
            offs.append([]), bals.append([]), lows.append([])
            flags.append(rec[3])
            state.append(rec[2])
            key ^= hash((rec[1], rec[2]))
            moved(rec[1], rec[2], off)
        else:
            _, tid, src, dst, amt = rec
            tid_at[tid], tid_rec[tid] = off, (src, dst, amt)
            for acc, new in ((src, state[src] - amt), (dst, state[dst] + amt)):
                key ^= hash((acc, state[acc])) ^ hash((acc, new))
                state[acc] = new
                moved(acc, new, off)
        for snap in want.get(key, ()):
            if off <= snap[0] and snap[1] == state:
                snap[2] = True
    for group in want.values():
        for dur, vec, found in group:
            stats["read_snapshot"] += 1
            if not found and vec:
                raise Failure("snapshot %s (durable %d) is not the state "
                              "after any prefix of the log" % (vec, dur))

    import bisect
    for thread in events:
        mine = {}               # account -> offset of my last OK on it
        told = set()
        for kind, tid, src, dst, amt, dur, rc in thread:
            if kind == K_XFER and rc in (RC_OK, RC_DUPLICATE):
                if tid_at.get(tid, INF) > dur:
                    raise Failure("transfer %d acknowledged with durable"
                                  "=%d, but its record ends at %s"
                                  % (tid, dur, tid_at.get(tid)))
                told.add(tid)
                for acc in (src, dst):
                    mine[acc] = max(mine.get(acc, 0), tid_at[tid])
            elif kind == K_XFER and rc == RC_INSUFFICIENT:
                stats["read_insufficient"] += 1
                i = bisect.bisect_right(offs[src], dur) - 1
                if flags[src] & 1 or i < 0 or lows[src][i] >= amt:
                    raise Failure("INSUFFICIENT_FUNDS for %d from account "
                                  "%d (durable %d), which never held less"
                                  " up to there" % (amt, src, dur))
            elif kind == K_BALANCE:
                stats["read_balance"] += 1
                seen = by_bal.get((src, amt), [])
                j = bisect.bisect_right(seen, bisect.bisect_right(
                    offs[src], dur) - 1) - 1
                if j < 0 or offs[src][seen[j]] < mine.get(src, 0):
                    raise Failure(
                        "balance %d of account %d (durable %d, own last "
                        "transfer at %d) matches no state of the log up "
                        "to there" % (amt, src, dur, mine.get(src, 0)))
            elif kind == K_LOOKUP and rc == RC_OK:
                stats["read_lookup"] += 1
                if tid_rec.get(tid) != (src, dst, amt) or tid_at[tid] > dur:
                    raise Failure("lookup of %d returned %s (durable %d);"
                                  " the log has %s ending at %s"
                                  % (tid, (src, dst, amt), dur,
                                     tid_rec.get(tid), tid_at.get(tid)))
            elif kind == K_LOOKUP:
                stats["read_lookup"] += 1
                if tid in told:
                    raise Failure("lookup of %d found nothing after the "
                                  "same thread was told it is applied"
                                  % tid)


class Epoch:
    """One ledger's life: its files and everything it has promised."""

    def __init__(self, args, directory, number):
        self.args = args
        self.dir = os.path.join(directory, "epoch%d" % number)
        os.makedirs(self.dir)
        self.wal = os.path.join(self.dir, "wal")
        self.acked = {}         # tid -> (src, dst, amount), OK/DUPLICATE
        self.safe_end = 0       # log offset known durable so far
        self.events = []        # this iteration: per thread, in order
        self.snaps = []         # this iteration: (durable, [balances])

    def run_and_kill(self, iteration, delay, rng):
        for name in os.listdir(self.dir):
            if name.startswith(("ack.", "snap.")):
                os.unlink(os.path.join(self.dir, name))
        a = self.args
        proc = subprocess.Popen(
            [a.worker, self.wal, self.dir, str(a.threads), str(a.accounts),
             str(iteration), str(a.seed), a.sync, "0"],
            stderr=subprocess.PIPE)
        time.sleep(delay)
        if proc.poll() is not None:
            raise Failure("worker exited by itself with status %d: %s"
                          % (proc.returncode, proc.stderr.read()[-500:]))
        proc.send_signal(signal.SIGKILL)
        err = proc.communicate()[1]
        if b"Sanitizer" in err:
            raise Failure("sanitizer report from the worker: %s"
                          % err[-3000:].decode(errors="replace"))

    def read_acks(self, stats):
        durable = 0
        self.events, self.snaps = [], []
        for name in sorted(os.listdir(self.dir)):
            with open(os.path.join(self.dir, name), "rb") as f:
                data = f.read()
            if name.startswith("snap."):
                off = 0
                while len(data) - off >= 16:
                    count, dur = struct.unpack_from("<QQ", data, off)
                    if len(data) - off < 16 + 8 * count:
                        break
                    self.snaps.append((dur, list(struct.unpack_from(
                        "<%dq" % count, data, off + 16))))
                    durable = max(durable, dur)
                    off += 16 + 8 * count
            if not name.startswith("ack."):
                continue
            data = data[:len(data) - len(data) % ACK.size]
            thread = []
            self.events.append(thread)
            for tid, src, dst, amt, dur, rc, kind in ACK.iter_unpack(data):
                durable = max(durable, dur)
                thread.append((kind, tid, src, dst, amt, dur, rc))
                if kind != K_XFER:
                    continue
                stats["returned"] += 1
                stats[RC_NAMES[rc]] += 1
                if rc in (RC_OK, RC_DUPLICATE):
                    old = self.acked.setdefault(tid, (src, dst, amt))
                    if old != (src, dst, amt):
                        raise Failure("id %d acknowledged with two "
                                      "different parameter sets" % tid)
        return durable

    def tear_tail(self, floor, rng, stats):
        """Damages only bytes at or after `floor`."""
        if not os.path.exists(self.wal):
            # Killed before the log file was even created.
            open(self.wal, "wb").close()
        size = os.path.getsize(self.wal)
        kind = rng.choice(["none", "none", "truncate", "truncate_to_floor",
                           "flip", "append", "truncate+append", "hole",
                           "rot"])
        if size < floor:
            # Killed before the 8-byte header was complete. There is
            # no tail yet, and bytes below `floor` are not ours to harm.
            kind = "none"
        elif size == floor and kind != "append":
            kind = "none"
        if kind == "rot":
            # Not a torn tail at all: one bit flips in a batch that has
            # a later batch behind it (acknowledged data). Recovery
            # must refuse; refused_as_it_must_be checks and restores.
            with open(self.wal, "rb") as f:
                data = f.read()
            ends, off = [], data.find(b"\x09\0\0\0", 8)
            while 0 <= off < walfmt.scan(data)[1] and len(ends) < 2:
                # (only the first two commit records are needed)
                if walfmt.one(data, off):
                    ends.append(off)
                off = data.find(b"\x09\0\0\0", off + 1)
            if len(ends) < 2:
                kind = "none"
            else:
                last = data.rfind(b"\x09\0\0\0", 0, walfmt.scan(data)[1]
                                  - walfmt.COMMIT_SIZE)
                pos = rng.randrange(8, max(last, ends[0]) + 1)
                data = bytearray(data)
                data[pos] ^= 1 << rng.randrange(8)
                with open(self.wal, "wb") as f:
                    f.write(data)
                if walfmt.verdict(bytes(data)) is None:
                    raise Failure("harness: rot at %d not refusable" % pos)
        stats["tail_" + kind] += 1
        with open(self.wal, "r+b") as f:
            if kind == "hole":
                # A block of the unsynced part that never reached the
                # disk although later bytes did.
                pos = rng.randrange(floor, size)
                f.seek(pos)
                f.write(b"\0" * min(size - pos, rng.choice([1, 17, 41, 512,
                                                           4096])))
            if kind == "truncate_to_floor":
                f.truncate(floor)
            if kind in ("truncate", "truncate+append"):
                f.truncate(rng.randrange(floor, size + 1))
            if kind == "flip" and size > floor:
                for _ in range(rng.randrange(1, 4)):
                    pos = rng.randrange(floor, size)
                    f.seek(pos)
                    byte = f.read(1)[0]
                    f.seek(pos)
                    f.write(bytes([byte ^ rng.randrange(1, 256)]))
            if "append" in kind:
                f.seek(0, 2)
                n = rng.choice([1, 7, 8, 40, 41, 300, 5000])
                junk = bytes(rng.randrange(256) for _ in range(n))
                f.write(rng.choice([junk, b"\0" * n]))

    def dump(self, path):
        res = subprocess.run([self.args.ledger, path, "dump"],
                             capture_output=True, text=True,
                             env=dict(os.environ, LEDGER_SYNC="process"))
        if res.returncode != 0:
            raise Failure("recovery refused the log: %s %s"
                          % (res.stdout, res.stderr))
        r, d = res.stdout.strip().split("\n")
        return int(r.split()[2]), d

    def refused_as_it_must_be(self, before, stats):
        """The damage just injected is of the kind recovery must not
        repair. Check that it refuses and changes nothing, then undo
        the damage (the operator restores the file)."""
        with open(self.wal, "rb") as f:
            torn = f.read()
        if len(torn) < 8 or walfmt.verdict(torn) is None:
            return
        res = subprocess.run([self.args.ledger, self.wal, "verify"],
                             capture_output=True, text=True,
                             env=dict(os.environ, LEDGER_SYNC="process"))
        with open(self.wal, "rb") as f:
            after = f.read()
        if res.returncode != 3 or "CORRUPT" not in res.stderr:
            raise Failure("damage in front of a later batch (%s) was not "
                          "refused: exit %d %s%s" % (
                              walfmt.verdict(torn), res.returncode,
                              res.stdout, res.stderr))
        if after != torn:
            raise Failure("a refused log was modified")
        stats["refused"] += 1
        with open(self.wal, "wb") as f:
            f.write(before)

    def recover_and_judge(self, floor, stats):
        with open(self.wal, "rb") as f:
            torn = f.read()
        if torn[:8] != walfmt.MAGIC[:len(torn[:8])] and torn.strip(b"\0"):
            raise Failure("log header damaged")
        py_end = walfmt.parse(torn)[1] if len(torn) >= 8 else 8
        copy = self.wal + ".copy"
        shutil.copyfile(self.wal, copy)
        cut_a, state_a = self.dump(copy)
        cut_b, state_b = self.dump(self.wal)
        cut_c, state_c = self.dump(self.wal)
        with open(self.wal, "rb") as f:
            data = f.read()
        with open(copy, "rb") as f:
            if f.read() != data:
                raise Failure("two recoveries left different log files")
        if len(data) != py_end:
            raise Failure("C cut the log at %d, the Python reader says "
                          "the valid part ends at %d" % (len(data), py_end))
        if len(data) < floor:
            raise Failure("recovery cut the log to %d, below the durable "
                          "offset %d" % (len(data), floor))
        if cut_c != 0 or cut_a != cut_b:
            raise Failure("recovery is not repeatable (%d, %d, %d bytes "
                          "cut)" % (cut_a, cut_b, cut_c))
        items, end = walfmt.scan(data)
        records = [rec for rec, _ in items]
        if end != len(data):
            raise Failure("recovered log has invalid bytes at %d" % end)
        fields = state_b.split()
        if fields[3] != "OK":
            raise Failure("lg_verify says %s" % fields[3])
        cells = [c.split(":") for c in fields[4:]]
        creates = [r for r in records if r[0] == walfmt.CREATE]
        if [r[1] for r in creates] != list(range(len(creates))):
            raise Failure("account ids in the log are not 0..n-1")
        applied = [r[1:] for r in records if r[0] == walfmt.XFER]
        check_all(Observation(
            balances=[int(c[0]) for c in cells],
            flags=[r[3] for r in creates],
            initials=[r[2] for r in creates],
            applied=applied, ordered=True, acked=self.acked,
            recovery_a=(state_a, state_c), recovery_b=(state_b, state_b)))
        if [int(c[1]) for c in cells] != [r[3] for r in creates]:
            raise Failure("account flags differ from the log")
        explain(items, self.events, self.snaps, stats)
        self.events, self.snaps = [], []
        stats["bytes_cut_by_recovery"] += cut_b
        self.safe_end = len(data)
        return len(applied)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--iterations", type=int, default=100)
    p.add_argument("--threads", type=int, default=16)
    p.add_argument("--accounts", type=int, default=24)
    p.add_argument("--sync", choices=["full", "process"], default="full")
    p.add_argument("--epoch", type=int, default=20)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--min-ms", type=float, default=1)
    p.add_argument("--max-ms", type=float, default=120)
    p.add_argument("--dir", default=None)
    p.add_argument("--worker",
                   default=os.path.join(ROOT, "build", "crash_worker"))
    p.add_argument("--ledger", default=os.path.join(ROOT, "build", "ledger"))
    args = p.parse_args()

    from collections import Counter
    stats = Counter()
    rng = random.Random(args.seed)
    base = tempfile.mkdtemp(prefix="ledger-crash-", dir=args.dir)
    transfers = 0
    epoch = None
    t0 = time.time()
    try:
        for it in range(args.iterations):
            if it % args.epoch == 0:
                if epoch:
                    transfers += in_log
                    shutil.rmtree(epoch.dir)
                epoch = Epoch(args, base, it // args.epoch)
            # One kill in five lands in the first milliseconds.
            if rng.random() < 0.2:
                delay = rng.uniform(0, 0.004)
            else:
                delay = rng.uniform(args.min_ms, args.max_ms) / 1000
            epoch.run_and_kill(it % args.epoch, delay, rng)
            stats["kills"] += 1
            floor = max(epoch.safe_end, epoch.read_acks(stats))
            before = b""
            if os.path.exists(epoch.wal):
                with open(epoch.wal, "rb") as f:
                    before = f.read()
            epoch.tear_tail(max(floor, 8), rng, stats)
            epoch.refused_as_it_must_be(before, stats)
            in_log = epoch.recover_and_judge(floor, stats)
        transfers += in_log
    except (Failure, AssertionError) as e:
        print("FAILED at iteration %d (seed %d): %s" % (it, args.seed, e))
        print("ledger kept in %s" % epoch.dir)
        sys.exit(1)
    shutil.rmtree(base)
    tails = ", ".join("%s=%d" % (k[5:], v) for k, v in sorted(stats.items())
                      if k.startswith("tail_"))
    print("crash harness: %d kill -9, sync=%s, %d threads, %.0f s"
          % (stats["kills"], args.sync, args.threads, time.time() - t0))
    print("  transfers that survived in the logs : %d" % transfers)
    print("  transfer calls that returned        : %d (OK=%d, "
          "DUPLICATE=%d, INSUFFICIENT_FUNDS=%d, INVALID=%d)"
          % (stats["returned"], stats["OK"], stats["DUPLICATE"],
             stats["INSUFFICIENT_FUNDS"], stats["INVALID"]))
    print("  tail damage injected                : %s" % tails)
    print("  bytes of torn tail cut by recovery  : %d"
          % stats["bytes_cut_by_recovery"])
    print("  damaged logs refused, then restored : %d" % stats["refused"])
    print("  reads explained by the durable log  : balance=%d, lookup=%d, "
          "snapshot=%d, INSUFFICIENT_FUNDS=%d"
          % (stats["read_balance"], stats["read_lookup"],
             stats["read_snapshot"], stats["read_insufficient"]))
    print("  I1-I6 held after every recovery     : yes (%d of %d)"
          % (stats["kills"], stats["kills"]))
    print("RESULT: %s transfers committed by %d threads across %d "
          "kill -9 crashes;\n        after every one of the %d "
          "recoveries all six invariants held."
          % (format(transfers, ","), args.threads, stats["kills"],
             stats["kills"]))


if __name__ == "__main__":
    main()
