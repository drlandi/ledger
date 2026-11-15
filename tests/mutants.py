"""Mutation check: are the tests able to notice a broken core? Each
mutant is a small bug planted in a copy of src/. A mutant is "killed"
when the test that should notice it fails. Survivors mean
the tests are blind to that bug.

Usage: python3 tests/mutants.py [logic|race|crash|recovery]
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
     "l->seeded > INT64_MAX - initial", "0"),
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
     [("l->acc[n].balance = initial;",
       "atomic_store(&l->count, n + 1); sched_yield(); sched_yield();"
       " l->acc[n].balance = initial;")]),
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

TOOL_SRC = {"driver": "tests/driver.c", "stress": "tests/stress.c",
            "crash_worker": "tests/crash_worker.c",
            "ledger": "cli/ledger_cli.c"}


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
        flags = SAN if tool == "driver" else ["-O2"]
        subprocess.run(CC + flags + ["-I" + MUT, "-o", exe,
                                     os.path.join(ROOT, TOOL_SRC[tool])]
                       + srcs, check=True)
        exes.append(exe)
    return exes


def killed_by(test, env):
    res = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s",
         os.path.join(ROOT, "tests"), "-p", test],
        env=dict(os.environ, **env), capture_output=True, text=True)
    return res.returncode != 0


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
    ]
    survivors = []
    total = 0
    for group, mutants, tools, test, env in groups:
        if only and only != group:
            continue
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
