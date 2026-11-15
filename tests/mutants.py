"""Mutation check: are the tests able to notice a broken core? Each
mutant is a small bug planted in a copy of src/. A mutant is "killed"
when the test that should notice it fails. Survivors mean
the tests are blind to that bug.

Usage: python3 tests/mutants.py
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUT = os.path.join(ROOT, "build", "mut")
CC = ["cc", "-std=c11", "-w", "-pthread",
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
     "&& a->amount == b->amount", ""),
    ("replay ignores destination", "transfer.c",
     "a->to == b->to &&", ""),
    ("different-params replay treated as duplicate", "transfer.c",
     "old->amount != 0 && xfer_same(old, x)", "old->amount != 0"),
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


def build(tool, name, fname, pairs):
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
    exe = os.path.join(MUT, tool)
    flags = SAN if tool == "driver" else ["-O2"]
    subprocess.run(CC + flags + ["-I" + MUT, "-o", exe,
                                 os.path.join(ROOT, "tests", tool + ".c")]
                   + srcs, check=True)
    return exe


def killed_by(test, env):
    res = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s",
         os.path.join(ROOT, "tests"), "-p", test],
        env=dict(os.environ, **env), capture_output=True, text=True)
    return res.returncode != 0


def main():
    survivors = []
    total = len(MUTANTS) + len(RACE_MUTANTS)
    for name, fname, old, new in MUTANTS:
        exe = build("driver", name, fname, [(old, new)])
        dead = killed_by("test_diff.py", {"DRIVER": exe, "DIFF_SEEDS": "500",
                                          "DIFF_TIMEOUT": "20"})
        print("%-9s %s" % ("killed" if dead else "SURVIVED", name),
              flush=True)
        if not dead:
            survivors.append(name)
    for name, fname, pairs in RACE_MUTANTS:
        exe = build("stress", name, fname, pairs)
        dead = killed_by("test_stress.py", {"STRESS": exe})
        print("%-9s %s (race)" % ("killed" if dead else "SURVIVED", name),
              flush=True)
        if not dead:
            survivors.append(name)
    shutil.rmtree(MUT, ignore_errors=True)
    print("%d/%d mutants killed" % (total - len(survivors), total))
    sys.exit(1 if survivors else 0)


if __name__ == "__main__":
    main()
