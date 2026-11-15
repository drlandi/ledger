"""Property-based differential test with shrinking (stretch goal M5).

Same property as test_diff.py - the C core and the Python model must
agree on every operation and on the final state - but the sequences
come from the `hypothesis` library, which, on a failure, shrinks the
sequence to a minimal one. test_diff.py reports "seed 673, operation
172"; this test reports the three or four operations that matter.

Optional: skipped when hypothesis is not installed
(`pip install hypothesis`). Not part of `make test`; run
`make test-property`.

Environment: DRIVER (default build/driver), PROPERTY_EXAMPLES
(default 400).
"""

import os
import unittest

import model as M
import test_diff as T

try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st
    HAVE = True
except ImportError:                                  # pragma: no cover
    HAVE = False

EXAMPLES = int(os.environ.get("PROPERTY_EXAMPLES", "400"))
MAX_ACCOUNTS = 4

if HAVE:
    # The strategy is shaped so that interesting states are common:
    # every case starts with a bank and up to three funded accounts,
    # most transfers use existing accounts, and amounts and balances
    # are small, so "amount == balance" and "amount == balance + 1"
    # happen all the time. A wilder branch keeps the edge values in.
    # (A first version drew everything independently; it produced a
    # successful transfer in under 1% of cases and found nothing.)
    I64 = st.integers(M.INT64_MIN, M.INT64_MAX)
    VALID = st.integers(0, 3)
    ANY = st.one_of(st.integers(0, MAX_ACCOUNTS),
                    st.sampled_from([2**32, 2**64 - 1]))
    WILD = st.one_of(st.integers(1, 300), st.sampled_from(T.EDGE), I64)
    TID = st.integers(0, 6)
    STEP = st.one_of(
        st.tuples(st.just("t"), TID, VALID, VALID, st.integers(1, 4)),
        st.tuples(st.just("t"), TID, VALID, VALID, st.integers(1, 4)),
        st.tuples(st.just("t"), TID, ANY, ANY, WILD),
        st.tuples(st.just("c"), st.one_of(st.integers(0, 4), WILD),
                  st.sampled_from([0, 0, 1, 2])),
        st.tuples(st.just("b"), ANY),
    )
    OPS = st.tuples(st.lists(st.integers(0, 4), max_size=3),
                    st.lists(STEP, max_size=30)).map(
        lambda t: [("c", 0, 1)] + [("c", v, 0) for v in t[0]] + t[1])


def run_case(ops):
    """Runs one operation list through model and C core. Returns
    (case, C output lines)."""
    c = T.Case(0)
    c.seed = "property"
    c.m = M.Model(MAX_ACCOUNTS)
    c.inp = ["r %d" % MAX_ACCOUNTS]
    c.exp = ["OK"]
    c.ids = set()
    for op in ops:
        c.inp.append(" ".join(str(v) for v in op))
        if op[0] == "c":
            rc, acc = c.m.create(op[1], op[2])
            c.exp.append("OK %d" % acc if rc == M.OK else rc)
        elif op[0] == "t":
            c.exp.append(c.m.transfer(*op[1:]))
            c.ids.add(op[1])
        else:
            rc, bal = c.m.balance(op[1])
            c.exp.append("OK %d" % bal if rc == M.OK else rc)
    c.finish()
    return c, T.run_driver([c])[:len(c.exp)]


@unittest.skipUnless(HAVE, "hypothesis is not installed")
class Property(unittest.TestCase):
    if HAVE:
        @settings(max_examples=EXAMPLES, deadline=None,
                  suppress_health_check=list(HealthCheck))
        @given(OPS)
        def test_c_core_matches_model(self, ops):
            case, got = run_case(ops)
            self.assertEqual(got, case.exp)
            T.judge_c_state(case, got)


if __name__ == "__main__":
    unittest.main()
