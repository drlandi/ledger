"""Mutation check: are the tests able to notice a broken core? Each
mutant is a small bug planted in a copy of src/. A mutant is "killed"
when the test that should notice it fails. Survivors mean
the tests are blind to that bug.

Usage: python3 tests/mutants.py [logic|race|crash|recovery|wal|reads|torn]
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUT = os.path.join(ROOT, "build", "mut")
CC = ["cc", "-std=c11", "-w", "-pthread", "-D_GNU_SOURCE",
      "-I" + os.path.join(ROOT, "include")]
SAN = ["-O1", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]

MUTANTS = [
    ("off-by-one in funds check", "transfer.c",
     "src->balance < x->amount", "src->balance <= x->amount"),
    ("funds check removed", "transfer.c",
     "src->balance < x->amount", "0"),
    ("overdraft flag ignored", "transfer.c",
     "!(src->flags & LG_ACC_OVERDRAFT) && src", "src"),
    ("credit overflow check removed", "transfer.c",
     "dst->balance > INT64_MAX - x->amount", "0"),
    ("debit underflow check removed", "transfer.c",
     "src->balance < INT64_MIN + x->amount", "0"),
    ("credit side dropped", "transfer.c",
     "l->acc[x->to].balance += x->amount;", ";"),
    ("id not remembered", "transfer.c",
     "*lg_idem_find(s, x->id) = *x;", ";"),
    ("replay ignores amount", "transfer.c",
     "if (old->amount != x->amount)", "if (0)"),
    ("replay ignores destination", "transfer.c",
     "|| old->to != x->to", ""),
    ("different-params replay treated as duplicate", "transfer.c",
     "if (old->from != x->from || old->to != x->to)", "if (0)"),
    ("zero amount accepted", "transfer.c",
     "x->amount <= 0", "x->amount < 0"),
    ("self-transfer accepted", "transfer.c",
     "|| x->from == x->to", ""),
    ("unknown destination accepted", "transfer.c",
     "|| x->to >= ", "|| x->to > "),
    ("hash probe skips a slot", "idem.c",
     "i = (i + 1) & (s->cap - 1);", "i = (i + 2) & (s->cap - 1);"),
    ("growth loses entries", "idem.c",
     "while (i < s->cap / 2)", "while (i < s->cap / 2 - 1)"),
    ("negative initial balance accepted", "account.c",
     "initial < 0 ||", ""),
    ("seeded overflow unchecked", "account.c",
     "l->seeded > INT64_MAX - (int64_t)c->from", "0"),
    ("account table overrun by one", "account.c",
     "n >= l->cfg.max_accounts", "n > l->cfg.max_accounts"),
]


LOCK = "pthread_mutex_lock(&l->acc[%s].lock);"
UNLOCK = "pthread_mutex_unlock(&l->acc[%s].lock);"

# Concurrency bugs: judged by the stress test, not the differential one.
RACE_MUTANTS = [
    ("account locks removed", "lock.c",
     [(LOCK % "a", ";"), (LOCK % "b", ";"),
      (UNLOCK % "a", ";"), (UNLOCK % "b", ";")]),
    ("only the lower account locked", "lock.c",
     [(LOCK % "b", ";"), (UNLOCK % "b", ";")]),
    ("idempotency shard lock removed", "lock.c",
     [("pthread_mutex_lock(&lg_shard(l, id)->lock);", ";"),
      ("pthread_mutex_unlock(&lg_shard(l, id)->lock);", ";")]),
    ("global mode takes no lock", "lock.c",
     [("pthread_mutex_lock(&l->global_lock);", ";"),
      ("pthread_mutex_unlock(&l->global_lock);", ";")]),
    ("snapshot takes no account locks", "snapshot.c",
     [("lg_lock_all(l, s->count);", ";"),
      ("lg_unlock_all(l, s->count);", ";")]),
    ("account published before it is initialised", "account.c",
     [("l->acc[n].balance = (int64_t)c->from;",
       "atomic_store(&l->count, n + 1); sched_yield(); sched_yield();"
       " l->acc[n].balance = (int64_t)c->from;")]),
]


# Durability bugs: judged by the crash tests (test_crash.py).
CRASH_MUTANTS = [
    ("OK returned before the record is durable", "wal_util.c",
     [("if (lg_wal_sync(l->wal, lsn) != 0)", "if (0)")]),
    ("batch marked durable before it is written", "wal.c",
     [("target = w->tail;", "target = w->tail; w->durable = target;")]),
    ("write error ignored", "wal.c",
     [("if (err != 0)", "if (0)")]),
    ("fdatasync skipped", "wal.c",
     [("w->sync_mode == LG_SYNC_FULL && fdatasync(w->fd) != 0", "0")]),
    ("record logged after the account locks are released", "transfer.c",
     [("*lsn = lg_log(l, LG_REC_XFER, x);", ";"),
      ("lg_unlock_pair(l, x->from, x->to);",
       "lg_unlock_pair(l, x->from, x->to); if (rc == LG_OK) {"
       " sched_yield(); *lsn = lg_log(l, LG_REC_XFER, x); }")]),
    ("torn tail not cut off", "wal_open.c",
     [("ftruncate(w->fd, (off_t)img->end) != 0 || fsync(w->fd) != 0",
       "0")]),
    ("two processes may open the same log", "wal_file.c",
     [("fd >= 0 && flock(fd, LOCK_EX | LOCK_NB) != 0", "0")]),
    ("log position not restored after recovery", "wal_open.c",
     [("if (lseek(w->fd, (off_t)img->end, SEEK_SET) < 0)", "if (0)")]),
    ("DUPLICATE answered before the original is durable", "transfer.c",
     [("*lsn = lg_tail(l);\n\treturn (LG_DUPLICATE);",
       "return (LG_DUPLICATE);")]),
]

# Recovery bugs: judged by the hand-built damaged logs
# (test_recovery.py).
RECOVERY_MUTANTS = [
    ("checksum not verified", "wal_rec.c",
     [("crc != want ||", "")]),
    ("checksum does not cover the length field", "wal_rec.c",
     [("crc = lg_crc32(tab, 0, p, 4);\n\tcrc = lg_crc32(tab, crc, p",
       "crc = lg_crc32(tab, 0, p, 4);\n\tcrc = lg_crc32(tab, 0, p"),
      ("crc = lg_crc32(tab, 0, out, 4);\n\tcrc = lg_crc32(tab, crc, out",
       "crc = lg_crc32(tab, 0, out, 4);\n\tcrc = lg_crc32(tab, 0, out")]),
    ("record length not bounded by the file", "wal_rec.c",
     [("if (avail < LG_REC_HDR + len)", "if (0)")]),
    ("impossible record skipped instead of refused", "recover.c",
     [("if (replay_one(l, &r) != LG_OK)", "if (0)")]),
    ("unlimited tail damage accepted", "recover.c",
     [("if (img->size - img->end > LG_WAL_BUF)", "if (0)")]),
    ("foreign file overwritten", "recover.c",
     [("if (hdr < 0)", "if (0)")]),
    ("torn tail left in place", "wal_open.c",
     [("else if (img->end < img->size)", "else if (0)")]),
    ("new account ids not checked on replay", "recover.c",
     [("if (id != r->x.id)", "if (0)")]),
]

# Gap 1: the buffer-full path of group commit. Judged by
# test_groupcommit.py (fdatasync gate + 128-byte-buffer storms).
FULL_LOOP = ("\twhile (w->len + n > LG_WAL_BUF - LG_COMMIT"
             " && !atomic_load(&w->failed))")
WAL_MUTANTS = [
    ("buffer full: records waiting in the buffer are dropped", "wal.c",
     [("\t\tw->full_waits++;\n\t\twal_step(w);",
       "\t\tw->full_waits++;\n\t\twal_step(w);\n\t\tw->len = 0;")]),
    ("buffer full: flushes without waiting for the running flush", "wal.c",
     [("\t\tw->full_waits++;\n\t\twal_step(w);",
       "\t\tw->full_waits++;\n\t\twal_flush(w);")]),
    ("buffer full: checked once, not again after waiting", "wal.c",
     [(FULL_LOOP, FULL_LOOP.replace("while", "if"))]),
    ("no room kept for the commit record", "wal.c",
     [("w->len + n > LG_WAL_BUF - LG_COMMIT", "w->len + n > LG_WAL_BUF")]),
    ("batch boundary: records that arrived during the flush count as "
     "durable", "wal.c",
     [("w->durable = target;", "w->durable = w->tail;")]),
    ("only one waiter woken after a flush", "wal.c",
     [("pthread_cond_broadcast(&w->cond);",
       "pthread_cond_signal(&w->cond);")]),
    ("batch written without its commit record", "wal.c",
     [("\tlg_wal_seal(w);", "\t;")]),
    ("commit record names the wrong batch start", "wal_batch.c",
     [("c.id = w->tail - w->len;", "c.id = w->tail;")]),
]

# Gap 3: answers given before the state they show is durable. Judged
# by test_groupcommit.py.
READ_MUTANTS = [
    ("lg_balance returns before the log is durable", "account.c",
     [("return (lg_settle(l, LG_OK, lsn));", "return (LG_OK);")]),
    ("lg_balance notes the log end before it has the account lock",
     "account.c",
     [("\tlg_lock_acc(l, id);\n\t*out = l->acc[id].balance;\n"
       "\tlsn = lg_tail(l);",
       "\tlsn = lg_tail(l);\n\tlg_lock_acc(l, id);\n"
       "\t*out = l->acc[id].balance;")]),
    ("lg_lookup returns before the log is durable", "lookup.c",
     [("return (lg_settle(l, rc, lsn));", "return (rc);")]),
    ("lg_snapshot returns before the log is durable", "snapshot.c",
     [("if (lg_settle(l, LG_OK, lsn) == LG_OK)", "if (1)")]),
    ("lg_snapshot notes the log end before it has the locks", "snapshot.c",
     [("\tlg_lock_all(l, s->count);\n\tsnap_copy(l, s);\n"
       "\tlsn = lg_tail(l);",
       "\tlsn = lg_tail(l);\n\tlg_lock_all(l, s->count);\n"
       "\tsnap_copy(l, s);")]),
    ("INSUFFICIENT_FUNDS answered before the debit behind it is durable",
     "transfer.c",
     [("\t\treturn (lg_reject(l, rc, lsn));", "\t\treturn (rc);")]),
    ("id conflict (INVALID) answered before the original is durable",
     "transfer.c",
     [("if (old->amount != x->amount)\n"
       "\t\treturn (lg_reject(l, LG_INVALID, lsn));",
       "if (old->amount != x->amount)\n\t\treturn (LG_INVALID);")]),
    ("answers derived from state wait only if they are OK or DUPLICATE",
     "wal_util.c",
     [("rc == LG_OK || rc == LG_DUPLICATE || lsn != 0",
       "rc == LG_OK || rc == LG_DUPLICATE")]),
]

# Gap 2: torn tail or corruption. Judged by test_torn.py.
TORN_MUTANTS = [
    ("damage is cut wherever it is (the old rule)", "recover.c",
     [("if (hdr == 1 && lg_scan_tail(w->crc, img, img->end) != 0)",
       "if (0)")]),
    ("bytes behind a damaged batch's own commit record are ignored",
     "wal_batch.c",
     [("return (-(r.x.id != end || p + n != img->size));",
       "return (-(r.x.id != end));")]),
    ("a commit record of another batch at the very end is ignored",
     "wal_batch.c",
     [("return (-(r.x.id != end || p + n != img->size));",
       "return (-(p + n != img->size));")]),
    ("tail scan cannot find a commit record behind a hole", "wal_batch.c",
     [("\t\tp++;", "\t\tp += LG_REC_HDR + LG_LEN_XFER;")]),
    ("a commit record is believed whatever batch start it names",
     "wal_batch.c",
     [("if (r.type == LG_REC_COMMIT && r.x.id != end)", "if (0)")]),
    ("records without a commit record are replayed", "wal_batch.c",
     [("\treturn (end);\n}", "\treturn (off);\n}")]),
    ("a tail of exactly one full batch is refused", "recover.c",
     [("img->size - img->end > LG_WAL_BUF",
       "img->size - img->end >= LG_WAL_BUF")]),
    ("commit records are replayed as if they were data records",
     "recover.c",
     [("\t\tif (r.type == LG_REC_COMMIT)\n\t\t\tcontinue ;",
       "\t\tif (r.type == LG_REC_COMMIT && r.x.id == 0)\n"
       "\t\t\tcontinue ;")]),
]

TINY = ["-DLG_WAL_BUF=128"]
TOOL_SRC = {"driver": "tests/driver.c", "stress": "tests/stress.c",
            "crash_worker": "tests/crash_worker.c",
            "crash_worker_tiny": "tests/crash_worker.c",
            "gate": "tests/gate.c",
            "ledger": "cli/ledger_cli.c"}
TOOL_FLAGS = {"driver": SAN, "crash_worker_tiny": ["-O2"] + TINY,
              "gate": ["-O2", "-Wl,--wrap=fdatasync"] + TINY}


def build(tools, name, fname, pairs):
    shutil.rmtree(MUT, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "src"), MUT)
    path = os.path.join(MUT, fname)
    text = open(path).read()
    for old, new in pairs:
        if text.count(old) != 1:
            sys.exit("mutant '%s': pattern not found exactly once: %s"
                     % (name, old))
        text = text.replace(old, new)
    open(path, "w").write(text)
    srcs = [os.path.join(MUT, f) for f in os.listdir(MUT)
            if f.endswith(".c")]
    exes = []
    for tool in tools:
        exe = os.path.join(MUT, tool)
        flags = TOOL_FLAGS.get(tool, ["-O2"])
        subprocess.run(CC + flags + ["-I" + MUT, "-o", exe,
                                     os.path.join(ROOT, TOOL_SRC[tool])]
                       + srcs, check=True)
        exes.append(exe)
    return exes


def killed_by(test, env):
    res = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-f", "-s",
         os.path.join(ROOT, "tests"), "-p", test],
        env=dict(os.environ, **env), capture_output=True, text=True)
    return res.returncode != 0


def GC_ENV(e):
    return {"GATE": e[0], "CRASH_WORKER_TINY": e[1], "LEDGER": e[2],
            "CRASH_ITER": "30"}


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    groups = [
        ("logic", [(n, f, [(o, w)]) for n, f, o, w in MUTANTS],
         ["driver"], "test_diff.py",
         lambda e: {"DRIVER": e[0], "DIFF_SEEDS": "500",
                    "DIFF_TIMEOUT": "20"}),
        ("race", RACE_MUTANTS, ["stress"], "test_stress.py",
         lambda e: {"STRESS": e[0]}),
        ("crash", CRASH_MUTANTS, ["crash_worker", "ledger"],
         "test_crash.py",
         lambda e: {"CRASH_WORKER": e[0], "LEDGER": e[1]}),
        ("recovery", RECOVERY_MUTANTS, ["ledger"], "test_recovery.py",
         lambda e: {"LEDGER": e[0]}),
        ("wal", WAL_MUTANTS, ["gate", "crash_worker_tiny", "ledger"],
         "test_groupcommit.py", GC_ENV),
        ("reads", READ_MUTANTS, ["gate", "crash_worker_tiny", "ledger"],
         "test_groupcommit.py", GC_ENV),
        ("torn", TORN_MUTANTS, ["ledger"], "test_torn.py",
         lambda e: {"LEDGER": e[0]}),
    ]
    # Fail fast if the core changed and a pattern no longer applies.
    for group, mutants, tools, test, env in groups:
        for name, fname, pairs in mutants:
            text = open(os.path.join(ROOT, "src", fname)).read()
            for old, new in pairs:
                if text.count(old) != 1:
                    sys.exit("mutant '%s': pattern not found exactly "
                             "once in %s: %s" % (name, fname, old))
    survivors = []
    total = 0
    for group, mutants, tools, test, env in groups:
        if only and only != group:
            continue
        # Control: the unchanged core, built and judged exactly like a
        # mutant, must pass. Otherwise "killed" would mean nothing.
        if killed_by(test, env(build(tools, "control", "ledger.c", []))):
            sys.exit("control failed: the unchanged core does not pass "
                     "%s in the mutant setup" % test)
        print("passed    [%s] control: unchanged core" % group, flush=True)
        for name, fname, pairs in mutants:
            total += 1
            dead = killed_by(test, env(build(tools, name, fname, pairs)))
            print("%-9s [%s] %s" % ("killed" if dead else "SURVIVED",
                                    group, name), flush=True)
            if not dead:
                survivors.append(name)
    shutil.rmtree(MUT, ignore_errors=True)
    print("%d/%d mutants killed" % (total - len(survivors), total))
    sys.exit(1 if survivors else 0)


if __name__ == "__main__":
    main()
