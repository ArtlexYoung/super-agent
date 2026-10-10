# 通用研究、算法与量化

Super Agent 的研究方向是：提出可检验的问题，取得证据，运行实验，保留反例，再交付经过验收的结果。适用于技术调研、算法设计、数据分析和离线量化研究；不提供攻击或渗透功能。

本批补充研究方法、实验比较器和可运行的离线示例。它是研究闭环的第一步，尚未证明真实模型的研究质量、长任务成功率或投资表现。

## 从 ARTEX 学到什么

2026-10-10 只读研究本地 `~/Code/project/ARTEX`，提交为 `5c0dbd4bec940f3c0faf20705a56d879b57d3dfe`。以下判断来自该快照的实际源码，不把在线延续仓库的后续功能算入本次结论。该快照通过 `go.mod` 引用外部 Norma，未包含其实现，本次没有审查或运行该外部依赖。

| ARTEX 中的设计与源码 | 通用价值 | Super Agent 的落点 |
| --- | --- | --- |
| `agent/goals.go` 区分最终目标和执行步骤；`planner.go` 按触发事件读取局部变化 | 避免把完成步骤当作达成目标；有新证据再调整方向 | 沿用 TaskQueue 的提交、验收、退回、交付；研究方法要求事件后重估假设 |
| `db/constants.go` / `exploration.go` 将目标、工作、事实及关系分开，事实也包含负结果 | 解释为什么继续某个方向，避免重复失败 | 方法要求保存主张、出处、反例、失败实验；本批不另建图数据库 |
| `agent/planner.go` 保留跨唤醒待办，并读取已完成工作的结果 | 长任务保留负责人和进度，依赖成立后才继续 | 复用现有任务依赖、共享板和等待机制；不增加第二套运行循环 |
| `agent/coldgraph.go` 保护活跃工作及其依赖；`compaction.go` / `db/digest.go` 保留摘要成员和内容版本 | 摘要是可失效的派生视图，不能替代原始证据 | 继续使用中央资源引用；图关系驱动的自动压缩留待长任务基线验证 |

ARTEX 的应用领域与本项目不同。本项目独立实现通用方法和比较器，没有复制其 Go 代码、提示词、领域 Skill 或工具，也没有引入其服务、数据库和部署依赖。许可证记录见[致谢与借鉴](acknowledgements.md)。

## 按需选择方法

| 引用 | 用途 | 关键交付 |
| --- | --- | --- |
| `plugin:super-agent/research` | 带来源的研究与选型 | 主张、出处、反证、结论范围和未确定事项 |
| `skill:super-agent/research/experiments` | 比较实验候选 | 固定协议、基线、原始测量、检查、保护指标和停止条件 |
| `skill:super-agent/research/algorithms` | 设计或优化算法 | 输入契约、正确性说明、独立 oracle、反例、复杂度和测量 |
| `skill:super-agent/research/quantitative` | 金融数据分析与离线回测 | 数据出处、因果时间、时间切分、成本、风险和局限 |

research 插件仍只默认加载研究入口和 citations。三个新方法可单独启用，由模型根据索引按需选择，或通过现有 CLI 指定：

```bash
super-agent --skill skill:super-agent/research/algorithms "分析这个算法，并给出可复现的验证方案"
super-agent --skill skill:super-agent/research/quantitative "检查这份回测的时间因果与费用假设"
```

模型、文件读取、联网、执行和数据源由宿主显式配置。方法可以帮助设计方案，但缺少对应工具时不能声称已读取来源、已测性能或已完成回测。普通 Agent 不因此创建团队、持久状态或后台进程。

## 宿主测量与候选比较

`super_agent.skill.experiments` 是标准库实现的可选纯函数模块，不执行代码，不调用模型，不写文件，也不替代任务验收：

```python
from super_agent.skill.experiments import (
    Experiment, ExperimentCriteria, MetricRule, compare_experiments,
)

criteria = ExperimentCriteria(
    objective=MetricRule("latency_ms", "minimize"),
    guardrails=(MetricRule("peak_memory_mb", "minimize", max_regression=2),),
    required_checks=("correctness", "boundary_cases"),
    minimum_samples=3,
    minimum_gain=1,
)
# baseline 和 candidates 必须由可信评测器实际执行后构造为 Experiment。
# 每项包含 protocol_sha256、data_sha256、artifacts、measurements、checks。
report = compare_experiments(baseline, candidates, criteria)
```

先固定标准再运行实验。协议摘要应覆盖评价器、环境、指标定义、按顺序排列的种子与时间切分；数据摘要标识确切输入；产物摘要标识被测实现和测量报告。宿主保留实际文件、命令、退出状态和原始记录，才能复现这些摘要。

比较器有以下行为：

- 基线证据不完整时抛错；失败候选保留在报告中，不静默丢弃。
- 缺少产物、必需检查、测量，或任何已登记检查失败，候选不能入选。
- 协议、数据或采样数量不同的候选标为不可比较；同一次记录内的必需指标采样数量也必须一致。
- 主指标取中位数，保护指标取观测样本中最差值；保护指标的退化容忍量与最低提升量均为指标的绝对单位。
- 平局与退化不会被当作提升；没有合格候选时明确返回 `selection.status = "none"`。同等提升按名称确定顺序，便于复现。
- 拒绝布尔值、NaN、无穷和数值溢出，不将它们排序成一个赢家。

这只是描述性比较，不提供显著性检验，也不能由三个种子推导普遍有效性。保护指标相对基线比较；绝对风险上限应由宿主登记为必需检查。

**信任边界：** SHA-256 格式正确不等于文件存在或评价器可信。比较器无法识别伪造测量；模型填写的数字、摘要和 `true` 不能变成独立证据。宿主必须自行执行、核对产物并生成检查结果。最终交付沿用现有 `TaskCheck`，绑定标准版本和提交时的产物快照；产物变化使旧验收失效。参考 [test_research_experiments.py](../tests/test_research_experiments.py) 的完整提交、失效、退回和交付集成用例。

## 可运行的离线示例

```bash
PYTHONPATH=src python3.11 examples/algorithm_research.py
PYTHONPATH=src python3.11 examples/quantitative_research.py
```

[算法示例](../examples/algorithm_research.py) 比较线性和二分 lower-bound，使用标准库 `bisect_left` 独立核对结果，在三个固定数据批次记录元素比较次数。排序后的数组是输入前提，排序成本未计入；这里的优势仅指该工作负载的比较次数，不是实际耗时提升。测试还穷举小数组，包括空输入、重复值和边界目标。

[量化示例](../examples/quantitative_research.py) 使用三组固定种子的合成序列，预先固定三期动量规则和 90/90 时间切分。信号在下一期生效，评测从空仓开始，计入仓位变化费用和末期平仓，输出净收益、回撤、换手与仓位。未来数据变化不应影响过去仓位；费用由独立手算案例核对。

量化示例没有参数拟合或搜索，不展示真实市场收益。它采用按目标敞口变化计费的简化模型，未计权重漂移引起的再平衡换手、价差、冲击、融资、借券和交易容量，不作为实盘会计或交易引擎。示例允许没有候选胜出，并保留拒绝原因。

## 后续可验收的迭代

| 批次 | 要解决的问题 | 进入条件与验收 |
| --- | --- | --- |
| 本批：实验基础 | 候选是否可比较、是否正确、是否真的改善 | 离线计算与反例测试；保持唯一 Runtime、零默认依赖 |
| 证据关联 | 哪个结论依赖哪个来源、实验和反例 | 先固定研究留出样本；来源缺失/过期或产物变更会使对应结论待复核，沿用任务事件与资源引用 |
| 自适应推进 | 下一步怎样降低不确定性，何时停止 | 用真实长任务比较再规划前后的通过率、费用、人工接管；团队保持可选，不以 Agent 数量证明能力 |
| 验证后积累 | 成功方法是否能复用到新任务 | 独立留出任务、失败场景、量化未知时间区间；只有收益得到证实才进入既有进化采用路径 |

“超级 Agent”需要由这些任务上的交付质量、复现率、人工接管和成本证明。本批没有运行付费模型或接入行情服务；联网研究、真实模型能力和量化有效性仍待相应集成评测。
