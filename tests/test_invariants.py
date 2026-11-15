"""Proves the judge is not asleep: each test builds a correct
observation, injects one specific violation, and requires the checker
to name exactly the invariant that was broken."""

import unittest

from invariants import InvariantViolation, Observation, check_all, violations


def good():
    """Bank (overdraft) funds A with 100, A pays B 30."""
    return Observation(
        balances=[-100, 70, 30],
        flags=[1, 0, 0],
        initials=[0, 0, 0],
        applied=[(1, 0, 1, 100), (2, 1, 2, 30)],
        ordered=True,
        acked={1: (0, 1, 100), 2: (1, 2, 30)},
        recovery_a=("dump", 1),
        recovery_b=("dump", 1),
    )


class CheckerCatchesInjectedViolations(unittest.TestCase):
    def names(self, o):
        return sorted({v.split()[0] for v in violations(o)})

    def test_good_observation_passes(self):
        check_all(good())

    def test_empty_observation_passes(self):
        check_all(Observation())

    def test_i1_money_created(self):
        o = good()
        o.balances[2] += 1
        self.assertIn("I1", self.names(o))

    def test_i1_money_destroyed(self):
        o = good()
        o.balances[1] -= 1
        self.assertIn("I1", self.names(o))

    def test_i2_negative_normal_account(self):
        o = good()
        o.balances = [-100, 110, -10]
        o.applied = None
        self.assertEqual(self.names(o), ["I2"])

    def test_i2_negative_allowed_only_with_overdraft_flag(self):
        o = good()
        o.flags[0] = 0
        self.assertIn("I2", self.names(o))

    def test_i2_transient_negative_inside_ordered_log(self):
        # Final state is fine, but B spent money before receiving it.
        o = good()
        o.applied = [(2, 1, 2, 30), (1, 0, 1, 100)]
        o.acked = None
        self.assertEqual(self.names(o), ["I2"])
        o.ordered = False
        self.assertEqual(self.names(o), [])

    def test_i3_transfer_applied_twice(self):
        o = good()
        o.applied.append((2, 1, 2, 30))
        o.balances = [-100, 40, 60]
        self.assertEqual(self.names(o), ["I3"])

    def test_i4_half_applied_transfer_debit_only(self):
        o = good()
        o.balances = [-100, 70, 0]
        self.assertEqual(self.names(o), ["I1", "I4"])

    def test_i4_balanced_but_unexplained(self):
        # Sum is right, nobody is negative, yet no transfer explains it.
        o = good()
        o.balances = [-100, 60, 40]
        self.assertEqual(self.names(o), ["I4"])

    def test_i4_account_count_mismatch(self):
        o = good()
        o.balances.append(0)
        o.flags.append(0)
        self.assertEqual(self.names(o), ["I4"])

    def test_i5_acked_transfer_lost(self):
        o = good()
        o.applied = [(1, 0, 1, 100)]
        o.balances = [-100, 100, 0]
        self.assertEqual(self.names(o), ["I5"])

    def test_i5_acked_transfer_came_back_different(self):
        o = good()
        o.acked[2] = (1, 2, 31)
        self.assertEqual(self.names(o), ["I5"])

    def test_i6_recoveries_differ(self):
        o = good()
        o.recovery_b = ("dump", 2)
        self.assertEqual(self.names(o), ["I6"])

    def test_check_all_raises_with_every_violation_listed(self):
        o = good()
        o.balances[2] += 1
        o.recovery_b = None
        with self.assertRaises(InvariantViolation) as ctx:
            check_all(o)
        msg = str(ctx.exception)
        for name in ("I1", "I4", "I6"):
            self.assertIn(name, msg)


if __name__ == "__main__":
    unittest.main()
