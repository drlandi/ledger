"""Runs TLC on every configuration in model/ and checks the outcome:
the correct design must pass all invariants; each planted bug must
make TLC report the named invariant as violated.

Usage: python3 model/check.py PATH/TO/tla2tools.jar [-v]
       -v also prints the counterexample traces of the planted bugs.
"""

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# configuration -> invariant TLC must report as violated (None: none)
RUNS = [
    ("ok_buf1", None),
    ("ok_buf2", None),
    ("ok_2crashes", None),
    ("ok_3threads", None),
    ("reach_full_buffer", "ProbeBlocked"),
    ("reach_full_buffer_in_flight", "ProbeInFlight"),
    ("unreachable_with_2_threads", None),
    ("bug_ack_before_flush", "I5"),
    ("bug_read_before_durable", "ObservedAfterCrash"),
    ("bug_reject_before_durable", "ObservedAfterCrash"),
]


def tlc(jar, cfg):
    env = dict(os.environ)
    env.pop("JAVA_TOOL_OPTIONS", None)
    with tempfile.TemporaryDirectory() as meta:
        res = subprocess.run(
            ["java", "-XX:+UseParallelGC", "-cp", jar, "tlc2.TLC",
             "-workers", "auto", "-metadir", meta, "-config",
             cfg + ".cfg", "MC.tla"],
            cwd=HERE, env=env, capture_output=True, text=True)
    return res.stdout + res.stderr


def trace(out):
    """The counterexample as one line per step: action, then the
    variables that matter for reading it."""
    steps = []
    for block in re.split(r"\nState \d+: ", out)[1:]:
        head = block.split("\n")[0]
        name = re.match(r"<(\w+)", head)
        get = lambda v: re.search(r"/\\ %s = (.*)" % v, block)
        steps.append("    %-11s disk=%s  buf=%s  acks=%s" % (
            name.group(1) if name else "Start",
            get("disk").group(1), get("buf").group(1),
            get("acks").group(1)))
    return "\n".join(steps)


def main():
    jar = os.path.abspath(sys.argv[1])
    verbose = "-v" in sys.argv
    bad = 0
    for cfg, want in RUNS:
        out = tlc(jar, cfg)
        states = re.search(r"(\d+) states generated, (\d+) distinct", out)
        got = re.search(r"Invariant (\w+) is violated", out)
        clean = "No error has been found" in out
        ok = (clean and want is None) or (got and got.group(1) == want)
        if want is None:
            line = "%s distinct states, depth %s, no invariant violated, " \
                   "no deadlock" % (states.group(2) if states else "?",
                                    re.search(r"search is (\d+)", out)
                                    .group(1) if clean else "?")
        else:
            n = len(re.findall(r"\nState \d+: ", out))
            line = "TLC found: invariant %s violated, trace of %d states" \
                   % (got.group(1) if got else "NONE", n)
            if want.startswith("Probe"):
                line = "reachable (probe violated, trace of %d states)" % n
        print("%-4s %-26s %s" % ("ok" if ok else "BAD", cfg, line),
              flush=True)
        if want and not want.startswith("Probe") and (verbose or not ok):
            print(trace(out) if ok else out[-3000:])
        bad += not ok
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
