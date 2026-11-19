# -*- coding: utf-8 -*-
"""10 미만 가리기와 역산 방지."""
import random
import unittest

from tests._base import ROOT  # noqa: F401

from app import masking as M


class Vector(unittest.TestCase):
    def test_below_ten_is_masked(self):
        r = M.mask_vector([120, 9, 10, 0, 45])
        self.assertEqual(r["mask"], [0, 1, 0, 1, 0])
        self.assertEqual(r["primary"], 2)

    def test_single_masked_cell_gets_a_partner(self):
        r = M.mask_vector([120, 3, 45, 60])
        self.assertEqual(r["mask"], [0, 1, 2, 0])          # 다음으로 작은 45 를 함께 가린다
        self.assertEqual((r["primary"], r["secondary"]), (1, 1))
        self.assertFalse(r["hide_total"])

    def test_lone_cell_hides_total(self):
        r = M.mask_vector([3])
        self.assertTrue(r["hide_total"])

    def test_total_below_ten_is_hidden(self):
        r = M.mask_vector([3, 4], total_basis=7)
        self.assertEqual(r["mask"], [1, 1])
        self.assertTrue(r["hide_total"])
        self.assertFalse(M.mask_vector([3, 4, 5], total_basis=12)["hide_total"])

    def test_nothing_to_mask(self):
        r = M.mask_vector([10, 11, 500])
        self.assertEqual(r["mask"], [0, 0, 0])

    def test_cell_text(self):
        self.assertEqual(M.cell_text(3, 1), "10 미만")
        self.assertEqual(M.cell_text(45, 2), "가림")
        self.assertEqual(M.cell_text(45, 0), 45)


class Matrix(unittest.TestCase):
    def test_rows_and_columns_never_keep_one_masked_cell(self):
        r = M.mask_matrix([[120, 3, 45], [80, 70, 60], [5, 90, 40]])
        self.assertEqual(r["primary"], 2)
        self.assertEqual(M.recoverable_cells(r["mask"], r["hide_row_total"], r["hide_col_total"]), 0)

    def test_random_tables_are_never_recoverable(self):
        rng = random.Random(7)
        for _ in range(300):
            nr, nc = rng.randint(1, 7), rng.randint(1, 8)
            basis = [[rng.choice([0, 1, 4, 9, 10, 12, 50, 300, 4000]) for _ in range(nc)] for _ in range(nr)]
            r = M.mask_matrix(basis)
            for i in range(nr):
                for j in range(nc):
                    if basis[i][j] < 10:
                        self.assertEqual(r["mask"][i][j], 1)
            self.assertEqual(M.recoverable_cells(r["mask"], r["hide_row_total"], r["hide_col_total"]), 0, basis)

    def test_hidden_row_total_from_another_table_gets_a_masked_cell(self):
        # 다른 표에서 가린 줄 합계(1번 줄)를 숨기면, 칸이 모두 보이는 한 그 합계를 더해서 알 수 있다 → 칸도 가린다.
        basis = [[120, 30, 45], [80, 70, 60], [50, 90, 40]]
        r = M.mask_matrix(basis, row_hidden=[False, True, False])
        self.assertEqual(r["hide_row_total"], [False, True, False])
        self.assertTrue(any(r["mask"][1]))
        self.assertEqual(M.recoverable_cells(r["mask"], r["hide_row_total"], r["hide_col_total"]), 0)
        plain = M.mask_matrix(basis)
        self.assertEqual(M.recoverable_cells(plain["mask"], [False, True, False], [False] * 3), 1)

    def test_random_tables_with_hidden_margins(self):
        rng = random.Random(11)
        for _ in range(300):
            nr, nc = rng.randint(2, 6), rng.randint(2, 7)
            basis = [[rng.choice([0, 3, 9, 10, 12, 50, 300, 4000]) for _ in range(nc)] for _ in range(nr)]
            rh = [rng.random() < 0.3 for _ in range(nr)]
            ch = [rng.random() < 0.3 for _ in range(nc)]
            r = M.mask_matrix(basis, row_hidden=rh, col_hidden=ch)
            for i in range(nr):
                self.assertTrue(r["hide_row_total"][i] or not rh[i])
            self.assertEqual(M.recoverable_cells(r["mask"], r["hide_row_total"], r["hide_col_total"]), 0, (basis, rh, ch))

    def test_grand_total_check(self):
        # 줄 합계 하나만 숨기고 총계를 보이면 총계에서 다른 줄 합계를 빼서 알 수 있다.
        mask = [[1, 1], [1, 1]]
        self.assertEqual(M.recoverable_cells(mask, [True, False], [False, False], hide_grand=False), 1)
        self.assertEqual(M.recoverable_cells(mask, [True, True], [False, False], hide_grand=False), 0)
        self.assertEqual(M.recoverable_cells(mask, [True, False], [False, False]), 0)


class Audit(unittest.TestCase):
    def test_single_unknown_in_a_sum_is_recoverable(self):
        rel = [("G", ["a", "b", "c"])]
        self.assertEqual(M.audit(rel, set(["a"])), 1)
        self.assertEqual(M.audit(rel, set(["a", "b"])), 0)
        self.assertEqual(M.audit(rel, set(["G"])), 1)

    def test_recovery_chains_across_tables(self):
        # 한 줄짜리 표에서 a 를 가렸어도 행렬의 줄 합계(= a)가 칸의 합으로 드러나면 b 까지 알 수 있다.
        rel = [("G", ["a", "b", "c"]), ("a", ["a1", "a2"])]
        self.assertEqual(M.audit(rel, set(["a", "b"])), 2)
        self.assertEqual(M.audit(rel, set(["a", "b", "a1"])), 0)


if __name__ == "__main__":
    unittest.main()
