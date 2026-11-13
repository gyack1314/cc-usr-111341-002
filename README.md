# 未回应人群的数字服务覆盖敏感性分析

本项目在资料完整性检查之上，提供面向发布稿的未回应敏感性分析。调查负责人
担心没回手机问卷的人正是不会用手机的人，因此结论不能只给一个修正百分比：
工具让发布者先看见结论依赖哪些未回应假设，再决定能否宣称“达到覆盖目标”。

## 分析口径（冻结）

所有口径在分析开始前冻结为带版本与摘要的对象（`coverage_data/calibration.py`），
报告回带口径版本与 SHA256 摘要，口径变化必须升版本：

- 目标时期与服务定义：本期至少成功使用一次数字服务；
- 行政记录仅对授权且身份可核对者可见，**没有记录不代表未使用**，
  成功记录才构成正面证据；冲突与无法确认的个体保留独立状态，不默认半数使用；
- 非随机未回应参数化：组内未回应者相对可比回应者的使用倾向 ρ 在冻结敏感域
  `[0.5, 2.0]` 内变化，ρ=1 即完全随机不回应（MAR）；
- 达标阈值 80%，集群重抽样 1000 次、固定随机种子。

## 两层范围分开陈列

- **抽样波动**：整组（cluster）bootstrap，同一人及其抽样组永远一起出现，
  只在固定 MAR 假设下量化抽样不确定性；识别不足的重抽样不填充默认使用率。
- **未回应假设范围**：在 ρ 敏感域与总体边际人数区间的**联合可行域**内，
  用线性分式结构端点枚举求总体及各组覆盖率可达范围。边际是闭区间且需共同
  满足，不会每组各取一个最坏端点拼成不可能的人群；同时给出朴素独立端点
  作为对照，显示联合约束收紧了多少。

识别不足（某组无任何带使用答案的回应者）与边际矛盾（联合可行域为空）
分别报告：前者放宽到仅由确定状态决定的证据界（Manski 型界），后者直接
判定无法计算并说明原因，均不制造精确答案。

## 翻转判断与追访安排

- 逐组寻找足以翻转达标判断的未回应倾向（临界 ρ），区分“全敏感域内均
  不达标”“单组下压不足以翻转”与可给出临界值的情形。
- 追访价值按“解决该个体后联合可达范围的预期收缩”逐人模拟，乘以其完整
  设计权重，联系次数与联系难易不参与排序；输出预算检查点上的收缩后范围。

## 资料表与运行

资料表包含 sample、contacts、usage、margins，字段见 `docs/data-contract.md`。
`data/normal.json` 为常规资料，`data/boundary.json` 含整组无人回应的边界
情形，`data/broken-digest.json` 用于检查资料损坏的拒绝行为。JSONL 按清单内
SHA256 与字节长度绑定。运行环境 Python 3.11+，仅用标准库。

数据文件是不可信输入，读取数据的进程以隔离模式 `-I` 启动，包路径由
`tools/run_analysis.py` 显式登记，防止数据目录同名模块被导入：

```sh
python -I tools/run_analysis.py inspect  data/normal.json   # 资料身份与完整性
python -I tools/run_analysis.py analyze  data/normal.json   # 敏感性联合分析报告
python -I tools/run_analysis.py person   data/normal.json --id u1   # 逐人追问
python -I -c "import sys;sys.path.insert(0,'.');import unittest; \
  unittest.TextTestRunner().run(unittest.TestLoader().discover('tests'))"
python -I -m compileall -q coverage_data tests tools
```

## 模块划分

- `calibration.py` 冻结口径与个体状态常量；
- `observe.py` 冻结状态机构造与逐人可追问记录；
- `margins.py` 总体边际区间登记（含地区/年龄/总体汇总边际）；
- `design.py` 抽样设计基准（HT 权重、MAR 率）与整组重抽样；
- `sensitivity.py` ρ 参数化、组率界、翻转判断；
- `structural.py` 盒约束+线性分式结构的联合范围端点枚举（主求解器）；
- `lpsolve.py` 两阶段单纯形（大规模回退与交叉验证）；
- `followup.py` 逐人追访价值与预算检查点；
- `analysis.py` 编排完整报告；`cli.py` 命令行入口。
