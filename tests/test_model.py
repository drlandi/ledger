"""Unit tests for the reference model, plus a random walk that hands
the model's own state to the shared invariant checker."""

import random
import unittest

import model as M
from invariants import Observation, check_all


def observe(m):
    return Observation(balances=list(m.balances), flags=list(m.flags),
                       initials=list(m.initials), applied=list(m.log),
                       ordered=True)


class ModelRules(unittest.TestCase):
    def setUp(self):
        self.m = M.Model(max_accounts=4)
        self.bank = self.m.create(0, M.FLAG_OVERDRAFT)[1]
        self.a = self.m.create(100)[1]
        self.b = self.m.create(0)[1]

    def test_create_assigns_dense_ids(self):
        self.assertEqual((self.bank, self.a, self.b), (0, 1, 2))
        self.assertEqual(self.m.create(5), (M.OK, 3))
        self.assertEqual(self.m.create(5), (M.FULL, None))

    def test_create_rejects_bad_input(self):
        self.assertEqual(self.m.create(-1), (M.INVALID, None))
        self.assertEqual(self.m.create(1, 2), (M.INVALID, None))
        self.assertEqual(self.m.create(M.INT64_MAX), (M.INVALID, None))

    def test_transfer_moves_money(self):
        self.assertEqual(self.m.transfer(1, self.a, self.b, 30), M.OK)
        self.assertEqual(self.m.balance(self.a), (M.OK, 70))
        self.assertEqual(self.m.balance(self.b), (M.OK, 30))

    def test_duplicate_changes_nothing(self):
        self.m.transfer(1, self.a, self.b, 30)
        self.assertEqual(self.m.transfer(1, self.a, self.b, 30), M.DUPLICATE)
        self.assertEqual(self.m.balance(self.a), (M.OK, 70))

    def test_duplicate_id_different_params_is_invalid(self):
        self.m.transfer(1, self.a, self.b, 30)
        self.assertEqual(self.m.transfer(1, self.a, self.b, 31), M.INVALID)
        self.assertEqual(self.m.transfer(1, self.b, self.a, 30), M.INVALID)
        self.assertEqual(self.m.balance(self.a), (M.OK, 70))

    def test_rejected_transfer_does_not_consume_the_id(self):
        self.assertEqual(self.m.transfer(1, self.b, self.a, 5),
                         M.INSUFFICIENT_FUNDS)
        self.m.transfer(2, self.a, self.b, 5)
        self.assertEqual(self.m.transfer(1, self.b, self.a, 5), M.OK)

    def test_exact_balance_is_enough(self):
        self.assertEqual(self.m.transfer(1, self.a, self.b, 100), M.OK)
        self.assertEqual(self.m.transfer(2, self.a, self.b, 1),
                         M.INSUFFICIENT_FUNDS)

    def test_invalid_and_unknown(self):
        self.assertEqual(self.m.transfer(1, self.a, self.b, 0), M.INVALID)
        self.assertEqual(self.m.transfer(1, self.a, self.b, -5), M.INVALID)
        self.assertEqual(self.m.transfer(1, self.a, self.a, 5), M.INVALID)
        self.assertEqual(self.m.transfer(1, self.a, 9, 5), M.UNKNOWN_ACCOUNT)
        self.assertEqual(self.m.transfer(1, 9, self.a, 5), M.UNKNOWN_ACCOUNT)
        self.assertEqual(self.m.balance(9), (M.UNKNOWN_ACCOUNT, None))

    def test_overdraft_account_may_go_negative(self):
        self.assertEqual(self.m.transfer(1, self.bank, self.b, 500), M.OK)
        self.assertEqual(self.m.balance(self.bank), (M.OK, -500))

    def test_overflow_is_rejected_both_ways(self):
        big = M.INT64_MAX
        self.assertEqual(self.m.transfer(1, self.bank, self.b, big), M.OK)
        self.assertEqual(self.m.transfer(2, self.bank, self.b, 1), M.INVALID)
        self.assertEqual(self.m.transfer(3, self.bank, self.a, 2), M.INVALID)
        self.assertEqual(self.m.transfer(4, self.bank, self.a, 1), M.OK)
        self.assertEqual(self.m.balance(self.bank), (M.OK, M.INT64_MIN))
        check_all(observe(self.m))


class ModelRandomWalk(unittest.TestCase):
    def test_invariants_hold_after_every_step(self):
        for seed in range(200):
            rng = random.Random(seed)
            m = M.Model(max_accounts=8)
            m.create(0, M.FLAG_OVERDRAFT)
            for _ in range(150):
                if rng.random() < 0.05:
                    m.create(rng.randrange(0, 1000))
                else:
                    m.transfer(rng.randrange(40), rng.randrange(9),
                               rng.randrange(9), rng.randrange(-5, 600))
                check_all(observe(m))


if __name__ == "__main__":
    unittest.main()
