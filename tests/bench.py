"""Throughput of the in-memory core: per-account locks vs one global
lock. Prints a markdown table. Median of REPS runs per cell.

Usage: python3 tests/bench.py [ops_per_thread]
"""

import os
import re
import statistics
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRESS = os.path.join(ROOT, "build", "stress")
OPS = int(sys.argv[1]) if len(sys.argv) > 1 else 1000000
REPS = 5
WORKLOADS = [
    ("uniform, 1024 accounts", ["--accounts", "1024", "--hot-pct", "0"]),
    ("hot: 80% on 2 of 1024", ["--accounts", "1024", "--hot", "2",
                               "--hot-pct", "80"]),
]


def rate(mode, threads, extra):
    runs = []
    for rep in range(REPS):
        out = subprocess.run(
            [STRESS, "--mode", mode, "--threads", str(threads),
             "--ops", str(OPS // threads), "--seed", str(rep + 1)] + extra,
            capture_output=True, text=True, check=True).stdout
        runs.append(float(re.search(r"ops_per_sec=(\d+)", out).group(1)))
    return statistics.median(runs)


def cpu():
    with open("/proc/cpuinfo") as f:
        names = [l.split(":")[1].strip() for l in f if "model name" in l]
    return "%d x %s" % (len(names), names[0] if names else "?")


def main():
    print("CPU: %s. %d transfers per run, median of %d runs, "
          "transfers/second." % (cpu(), OPS, REPS))
    for title, extra in WORKLOADS:
        print("\n%s\n" % title)
        print("| threads | global lock | per-account locks | ratio |")
        print("|---:|---:|---:|---:|")
        for threads in (1, 2, 4, 8):
            g = rate("global", threads, extra)
            f = rate("fine", threads, extra)
            print("| %d | %s | %s | %.2fx |"
                  % (threads, format(int(g), ","), format(int(f), ","),
                     f / g), flush=True)
    durable()


def durable():
    """Same tool with a write-ahead log underneath (new file per run)."""
    import tempfile
    print("\nWith the write-ahead log (per-account locks, uniform "
          "traffic, 64 accounts). Each\ntransfer returns only after "
          "its record is written (process) or written and\nfdatasync'ed "
          "(full). %d transfers per run, median of 3.\n" % (OPS // 25))
    print("| threads | sync=process | sync=full | transfers per "
          "fdatasync |")
    print("|---:|---:|---:|---:|")
    with tempfile.TemporaryDirectory(dir=os.environ.get("BENCH_DIR")) as d:
        n = 0
        for threads in (1, 4, 16, 64, 256):
            cells = []
            for sync in ("process", "full"):
                runs = []
                for rep in range(3):
                    n += 1
                    out = subprocess.run(
                        [STRESS, "--threads", str(threads), "--ops",
                         str(OPS // 25 // threads), "--accounts", "64",
                         "--hot-pct", "0", "--seed", str(rep + 1),
                         "--wal", os.path.join(d, "wal%d" % n),
                         "--sync", sync],
                        capture_output=True, text=True, check=True).stdout
                    os.unlink(os.path.join(d, "wal%d" % n))
                    rate_ = float(re.search(r"ops_per_sec=(\d+)",
                                            out).group(1))
                    flushes = int(re.search(r"flushes=(\d+)",
                                            out).group(1))
                    runs.append((rate_, flushes))
                runs.sort()
                cells.append(runs[1])
            print("| %d | %s | %s | %.1f |"
                  % (threads, format(int(cells[0][0]), ","),
                     format(int(cells[1][0]), ","),
                     OPS // 25 / max(1, cells[1][1])), flush=True)


if __name__ == "__main__":
    main()
