"""单纯形求解器测试：最优性、不可行、无界、退化与冗余约束。"""
import unittest

from coverage_data.lpsolve import solve, LPInfeasible, LPUnbounded


class LpSolverTests(unittest.TestCase):
    def test_equality_only(self):
        value, x = solve([1, 1], a_eq=[[1, 1]], b_eq=[1], sense='min')
        self.assertAlmostEqual(value, 1.0)
        self.assertAlmostEqual(sum(x), 1.0)

    def test_inequality_max(self):
        value, x = solve([1], a_ub=[[1]], b_ub=[1], sense='max')
        self.assertAlmostEqual(value, 1.0)
        self.assertAlmostEqual(x[0], 1.0)

    def test_greater_than_via_negative_rhs(self):
        # min 2x+3y s.t. x+y>=10, x<=15 -> 20 at (10,0)
        value, x = solve([2, 3], a_ub=[[-1, -1], [1, 0]],
                         b_ub=[-10, 15], sense='min')
        self.assertAlmostEqual(value, 20.0)
        self.assertAlmostEqual(x[0], 10.0)
        self.assertAlmostEqual(x[1], 0.0)

    def test_classic_product_mix(self):
        # max 3x+5y s.t. x<=4, 2y<=12, 3x+5y<=25
        # 最优顶点 (4, 2.6)，目标值 25
        value, x = solve([3, 5], a_ub=[[1, 0], [0, 2], [3, 5]],
                         b_ub=[4, 12, 25], sense='max')
        self.assertAlmostEqual(value, 25.0)
        self.assertAlmostEqual(x[0], 4.0)
        self.assertAlmostEqual(x[1], 2.6)

    def test_infeasible_detected(self):
        with self.assertRaises(LPInfeasible):
            solve([1], a_ub=[[1]], b_ub=[0], a_eq=[[1]], b_eq=[1])

    def test_negative_equality_rhs_infeasible(self):
        with self.assertRaises(LPInfeasible):
            solve([1], a_eq=[[1]], b_eq=[-1])

    def test_unbounded_detected(self):
        # max x s.t. x>=1，无上界
        with self.assertRaises(LPUnbounded):
            solve([1], a_ub=[[-1]], b_ub=[-1], sense='max')

    def test_redundant_equality_rows(self):
        value, x = solve([1, 1], a_eq=[[1, 1], [2, 2]],
                         b_eq=[2, 4], sense='min')
        self.assertAlmostEqual(value, 2.0)

    def test_degenerate_equality_system(self):
        value, x = solve([1, 0], a_eq=[[1, 1], [0, 1]],
                         b_eq=[1, 0], sense='min')
        self.assertAlmostEqual(value, 1.0, places=5)
        self.assertAlmostEqual(x[0], 1.0, places=5)
        self.assertAlmostEqual(x[1], 0.0, places=5)

    def test_charnes_cooper_handled_by_structural_solver(self):
        # Charnes-Cooper 盒约束+等式结构由专用结构求解器处理，
        # 避免通用单纯形在零右端比例约束上的退化停滞。
        from coverage_data.structural import structural_range
        from coverage_data.margins import Margin
        rate_bounds = [(0.2, 0.5, 10, 20), (0.3, 0.6, 5, 8)]
        keys = [('A', 'x'), ('B', 'x')]
        total = Margin(('*', '*'), 15.0, 28.0, 'm9')
        result = structural_range(rate_bounds, [], [total], keys)
        self.assertNotIn('infeasible', result)
        self.assertGreaterEqual(result['overall_max'], result['overall_min'])
        self.assertLessEqual(result['overall_max'], 0.6 + 1e-9)
        self.assertGreaterEqual(result['overall_min'], 0.2 - 1e-9)
        for pop in (result['lp_min_population'], result['lp_max_population']):
            self.assertTrue(10 - 1e-9 <= pop[0] <= 20 + 1e-9)
            self.assertTrue(5 - 1e-9 <= pop[1] <= 8 + 1e-9)

    def test_structural_solver_detects_margin_conflict(self):
        from coverage_data.structural import structural_range
        from coverage_data.margins import Margin
        rate_bounds = [(0.0, 1.0, 20, 25), (0.0, 1.0, 30, 30)]
        keys = [('A', 'old'), ('A', 'young')]
        impossible = Margin(('*', '*'), 1000.0, 2000.0, 'm9')
        result = structural_range(rate_bounds, [], [impossible], keys)
        self.assertTrue(result.get('infeasible'))


if __name__ == '__main__':
    unittest.main()
