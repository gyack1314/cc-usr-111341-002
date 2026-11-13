"""纯标准库线性规划求解：两阶段单纯形（Bland + 退化重启）。

不引入外部依赖，问题规模为年龄地区组数（个位数到几十）。

退化处理
    本项目 Charnes-Cooper 线性规划含大量右端为 0 的比例约束，配等式
    约束与人工变量。零右端使最小比值全为 0，单纯形可能收敛到人工变量
    仍在基的退化顶点（目标行已无负系数但 w>0）。检测到该死锁时，对
    在基人工变量行做保可行的换基旋转并重启优化；若人工变量取值显著
    非零且不存在保可行换入列，则联合可行域确实为空。

不可行与无界分别以异常类型区分，调用方据此区分“边际矛盾（联合可行域
为空）”与参数化缺少必要的界。
"""

EPS = 1e-10
PIVOT_LIMIT = 10000
FEAS_TOL = 1e-7
# Dantzig-Wolfe 字典序扰动：零右端约束 b_i 替换为 b_i + d_i，
# d_i 按行号几何衰减且严格为正，配合 Wolfe 字典序离基规则，
# 保证单纯形在退化系统下有限终止、人工变量能正常离基。
# 首项 1e-6、公比 0.1，累计约 1.1e-6，远小于业务整数边际粒度 1，
# 不改变回带结论，仅改变离基选择路径。
PERTURB_FIRST = 1e-6
PERTURB_RATIO = 0.1


class LPInfeasible(ValueError):
    """联合可行域为空：边际约束互相矛盾。"""


class LPUnbounded(ValueError):
    """问题无界：参数化缺少必要的界。"""


def solve(c, a_eq=None, b_eq=None, a_ub=None, b_ub=None, sense='min'):
    """求解标准形线性规划，返回 (最优目标值, 原变量解)。"""
    n = len(c)
    a_ub = [list(row) for row in (a_ub or [])]
    b_ub = list(b_ub or [])
    a_eq = [list(row) for row in (a_eq or [])]
    b_eq = list(b_eq or [])

    parsed = []
    for row, b in zip(a_ub, b_ub):
        if b >= -EPS:
            parsed.append(('le', row, max(b, 0.0)))
        else:
            parsed.append(('ge', [-v for v in row], -b))
    for row, b in zip(a_eq, b_eq):
        if b < -EPS:
            parsed.append(('eq', [-v for v in row], -b))
        else:
            parsed.append(('eq', row, b))

    n_le = sum(1 for t, _, _ in parsed if t == 'le')
    n_ge = sum(1 for t, _, _ in parsed if t == 'ge')
    n_eq = sum(1 for t, _, _ in parsed if t == 'eq')
    n_art = n_ge + n_eq
    width = n + n_le + n_ge + n_art
    col_le = n
    col_surplus = n + n_le
    col_art = n + n_le + n_ge

    tableau, basis, art_row_cols = [], [], []
    le_seen = ge_seen = art_seen = 0
    for row_index, (kind, row, b) in enumerate(parsed):
        # 零右端字典序扰动，打破退化平局；非零右端保持原值
        if b <= EPS:
            b = b + PERTURB_FIRST * (PERTURB_RATIO ** row_index)
        trow = [0.0] * width + [b]
        for j in range(n):
            trow[j] = row[j]
        if kind == 'le':
            trow[col_le + le_seen] = 1.0
            basis.append(col_le + le_seen)
            le_seen += 1
        elif kind == 'ge':
            trow[col_surplus + ge_seen] = -1.0
            trow[col_art + art_seen] = 1.0
            basis.append(col_art + art_seen)
            art_row_cols.append((len(tableau), col_art + art_seen))
            ge_seen += 1
            art_seen += 1
        else:
            trow[col_art + art_seen] = 1.0
            basis.append(col_art + art_seen)
            art_row_cols.append((len(tableau), col_art + art_seen))
            art_seen += 1
        tableau.append(trow)

    art_cols = set(range(col_art, width))
    non_art_width = col_art

    if n_art:
        # 第一阶段：min Σ人工变量。零右端比例约束会造成退化，Bland
        # 可能停滞在人工变量仍在基、目标行已无负系数的顶点。检测到
        # 停滞时用对偶旋转救援（人工行离基、其负系数列进基），再重新
        # 优化，直到人工变量离基或确认无法消除（不可行）。
        tableau.append(_phase1_objective(tableau, art_row_cols, width))
        # Bland 主优化；停滞（人工变量仍在基且目标行无负系数）时，
        # 依次尝试人工行的每个负系数列做对偶旋转并重新优化，再用原始
        # 约束校验裁决：找到满足约束的基即退化可行，全部尝试失败才不可行。
        _pivot(tableau, basis, forbidden=art_cols)
        stall = [i for i, c in enumerate(basis) if c in art_cols]
        if stall:
            rescued = _rescue_artificial(
                tableau, basis, art_cols, art_row_cols, non_art_width,
                parsed, n, col_le, col_surplus, width)
            if not rescued:
                raise LPInfeasible('人工变量无法在可行域内消除：边际联合可行域为空')
        tableau.pop()
        keep_cols = [j for j in range(width) if j not in art_cols]
        tableau = [[row[j] for j in keep_cols] + [row[-1]] for row in tableau]
        remap = {old: new for new, old in enumerate(keep_cols)}
        basis = [remap[col] for col in basis]
        width2 = len(keep_cols)
        # 删除退化冗余零约束行及其专属基列；原变量列（列号<n）绝不删除，
        # 只删除松弛/剩余辅助列，避免解向量维度被破坏。
        drop_cols = set()
        i = 0
        while i < len(basis):
            basic_col = basis[i]
            nonbasic_zero = all(abs(tableau[i][j]) <= EPS
                                for j in range(width2) if j != basic_col)
            if nonbasic_zero and abs(tableau[i][-1]) <= FEAS_TOL \
                    and basic_col >= n:
                drop_cols.add(basic_col)
                del tableau[i]
                del basis[i]
                continue
            i += 1
        if drop_cols:
            keep = [j for j in range(width2) if j not in drop_cols]
            tableau = [[row[j] for j in keep] + [row[-1]] for row in tableau]
            remap2 = {old: new for new, old in enumerate(keep)}
            basis = [remap2[col] for col in basis]
            width2 = len(keep)
    else:
        width2 = width

    sign = 1.0 if sense == 'min' else -1.0
    obj = [0.0] * width2 + [0.0]
    for j in range(n):
        obj[j] = sign * c[j]
    tableau.append(obj)
    for i, col in enumerate(basis):
        if abs(tableau[-1][col]) > EPS:
            factor = tableau[-1][col]
            tableau[-1] = [v - factor * pv
                           for v, pv in zip(tableau[-1], tableau[i])]
    _pivot(tableau, basis, forbidden=set())

    x_full = [0.0] * width2
    for i, col in enumerate(basis):
        x_full[col] = tableau[i][-1]
    x = x_full[:n]
    value = -tableau[-1][-1] * sign
    return value, x


def _phase1_objective(tableau, art_row_cols, width):
    """min Σ人工变量 的目标行，行消去使人工列归零。"""
    obj = [0.0] * width + [0.0]
    for _, art_col in art_row_cols:
        obj[art_col] = 1.0
    for row_i, art_col in art_row_cols:
        if row_i >= len(tableau):
            continue
        factor = obj[art_col]
        if abs(factor) > EPS:
            obj = [v - factor * pv for v, pv in zip(obj, tableau[row_i])]
    return obj


def _restore_primal_feasibility(tableau, basis, forbidden):
    """原始单纯形恢复可行性：在不产生新负右端的前提下优化目标行。

    与常规 _pivot 的区别是离基比值必须非负（过滤掉会加剧不可行的
    负右端行），用于退化救援后把基解拉回可行域。返回是否全部右端非负。
    """
    for _ in range(PIVOT_LIMIT):
        if all(tableau[i][-1] >= -FEAS_TOL for i in range(len(tableau) - 1)):
            return True
        enter = next((j for j in range(len(tableau[-1]) - 1)
                      if j not in forbidden and tableau[-1][j] < -EPS), None)
        if enter is None:
            return False
        candidates = [i for i in range(len(tableau) - 1)
                      if tableau[i][enter] > EPS
                      and tableau[i][-1] / tableau[i][enter] >= -FEAS_TOL]
        if not candidates:
            return False
        ratios = [(tableau[i][-1] / tableau[i][enter], basis[i], i)
                  for i in candidates]
        _, _, leave = min(ratios)
        _do_pivot(tableau, basis, leave, enter)
    return False


def _rescue_artificial(tableau, basis, art_cols, art_row_cols,
                       non_art_width, parsed, n, col_le, col_surplus, width):
    """退化停滞时把人工变量驱出基并恢复到满足原始约束的可行基。

    对在基人工变量依次尝试负系数列做对偶旋转，再用原始单纯形恢复可行；
    每步以原始约束校验裁决，全部尝试失败则恢复快照返回 False。
    """
    snapshot_table = [row[:] for row in tableau]
    snapshot_basis = basis[:]

    for _ in range(PIVOT_LIMIT):
        art_idx = [i for i, c in enumerate(basis) if c in art_cols]
        if not art_idx:
            break
        ai = art_idx[0]
        neg_cols = [j for j in range(non_art_width)
                    if tableau[ai][j] < -EPS]
        if not neg_cols:
            if tableau[ai][-1] > FEAS_TOL:
                tableau[:] = snapshot_table
                basis[:] = snapshot_basis
                return False
            del tableau[ai]
            del basis[ai]
            continue
        rescued = False
        for enter in neg_cols:
            trial_t = [row[:] for row in tableau]
            trial_b = basis[:]
            _do_pivot(trial_t, trial_b, ai, enter)
            trial_t[-1] = _phase1_objective(trial_t, art_row_cols, width)
            _restore_primal_feasibility(trial_t, trial_b, art_cols)
            if _basis_satisfies_constraints(
                    trial_t, trial_b, non_art_width, parsed, n,
                    col_le, col_surplus):
                tableau[:] = trial_t
                basis[:] = trial_b
                rescued = True
                break
        if not rescued:
            tableau[:] = snapshot_table
            basis[:] = snapshot_basis
            return False
    ok = _basis_satisfies_constraints(
        tableau, basis, non_art_width, parsed, n, col_le, col_surplus)
    if not ok:
        tableau[:] = snapshot_table
        basis[:] = snapshot_basis
    return ok


def _basis_satisfies_constraints(tableau, basis, non_art_width, parsed,
                                 n, col_le, col_surplus):
    """回代当前基解校验全部原始约束与变量非负性。

    对偶救援在退化系统下可能产生表面 w=0 但实际偏离可行域的基，
    仅靠人工变量之和无法识别，必须用原始约束做最终裁决。
    """
    # 当前基变量取值（表右端）；非基变量为 0
    values = [0.0] * non_art_width
    for i, col in enumerate(basis):
        if col < non_art_width:
            values[col] = tableau[i][-1]
    # 所有变量（原变量、松弛、剩余）非负
    if any(v < -FEAS_TOL for v in values):
        return False
    x = values[:n]
    # 逐条回代原始约束
    le_seen = ge_seen = 0
    for kind, row, b in parsed:
        lhs = sum(row[j] * x[j] for j in range(n))
        if kind == 'le':
            # lhs + slack = b
            slack = values[col_le + le_seen]
            le_seen += 1
            if abs(lhs + slack - b) > 1e-6 or slack < -FEAS_TOL:
                return False
        elif kind == 'ge':
            # lhs - surplus = b
            surplus = values[col_surplus + ge_seen]
            ge_seen += 1
            if abs(lhs - surplus - b) > 1e-6 or surplus < -FEAS_TOL:
                return False
        else:
            if abs(lhs - b) > 1e-6:
                return False
    return True


def _pivot(tableau, basis, forbidden):
    for _ in range(PIVOT_LIMIT):
        obj = tableau[-1]
        # Bland 进基：最小下标负系数
        enter = None
        for j in range(len(obj) - 1):
            if j in forbidden:
                continue
            if obj[j] < -EPS:
                enter = j
                break
        if enter is None:
            return
        candidates = [i for i in range(len(tableau) - 1)
                      if tableau[i][enter] > EPS]
        if not candidates:
            raise LPUnbounded('单纯形找不到离基变量，问题无界')
        # 严格 Bland 离基：最小非负比值，平局取最小基变量下标。
        # 右端字典序扰动使零右端约束的比值严格可分，从而既保持旋转后
        # 右端非负，又在退化下有限终止、避免循环。
        ratios = [(tableau[i][-1] / tableau[i][enter], i) for i in candidates]
        _, leave = min(ratios, key=lambda t: (t[0], basis[t[1]]))
        _do_pivot(tableau, basis, leave, enter)
    raise RuntimeError('单纯形迭代超过上限，可能存在循环')


def _do_pivot(tableau, basis, leave, enter):
    pivot_row = tableau[leave]
    pivot = pivot_row[enter]
    tableau[leave] = [v / pivot for v in pivot_row]
    for i, row in enumerate(tableau):
        if i == leave:
            continue
        factor = row[enter]
        if abs(factor) > EPS:
            tableau[i] = [v - factor * pv for v, pv in zip(row, tableau[leave])]
    basis[leave] = enter
