"""Mutation check: is the differential test able to notice a broken
core? Each mutant is a one-line bug planted in a copy of src/. A
mutant is "killed" when test_diff fails against it. Survivors mean
the tests are blind to that bug.

Usage: python3 tests/mutants.py
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUT = os.path.join(ROOT, "build", "mut")
CC = ["cc", "-std=c11", "-O1", "-w", "-pthread",
      "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
      "-I" + os.path.join(ROOT, "include")]

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
     "count >= l->cfg.max_accounts", "count > l->cfg.max_accounts"),
]


def build(name, fname, old, new):
    shutil.rmtree(MUT, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "src"), MUT)
    path = os.path.join(MUT, fname)
    text = open(path).read()
    if text.count(old) != 1:
        sys.exit("mutant '%s': pattern not found exactly once" % name)
    open(path, "w").write(text.replace(old, new))
    srcs = [os.path.join(MUT, f) for f in os.listdir(MUT)
            if f.endswith(".c")]
    exe = os.path.join(MUT, "driver")
    subprocess.run(CC + ["-I" + MUT, "-o", exe,
                         os.path.join(ROOT, "tests", "driver.c")] + srcs,
                   check=True)
    return exe


def main():
    survivors = []
    for name, fname, old, new in MUTANTS:
        exe = build(name, fname, old, new)
        env = dict(os.environ, DRIVER=exe, DIFF_SEEDS="500", DIFF_TIMEOUT="20")
        res = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s",
             os.path.join(ROOT, "tests"), "-p", "test_diff.py"],
            env=env, capture_output=True, text=True)
        killed = res.returncode != 0
        print("%-9s %s" % ("killed" if killed else "SURVIVED", name),
              flush=True)
        if not killed:
            survivors.append(name)
    shutil.rmtree(MUT, ignore_errors=True)
    print("%d/%d mutants killed" % (len(MUTANTS) - len(survivors),
                                    len(MUTANTS)))
    sys.exit(1 if survivors else 0)


if __name__ == "__main__":
    main()
