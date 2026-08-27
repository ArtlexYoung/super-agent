# 学习、记忆与 Skill 更新 / Learning, Memory, and Skill Changes

## 记忆 / Memory

临时记忆只属于指定会话，用于本轮工作上下文；长期记忆保存抽象事实、重要偏好和稳定习惯。两者不会自动混写。

Temporary memory belongs to one conversation and supports current-turn context; long-term memory stores abstract facts, important preferences, and stable habits. They are not mixed automatically.

```python
user.memory.remember_temporary("本轮使用 JSONL", conversation_id=conversation_id)
item = user.memory.remember_long_term("用户偏好轻量、无依赖的默认配置")
user.memory.recall("默认配置")
user.memory.forget(item.memory_id, "用户明确要求遗忘")
```

模型可以通过记忆 Skill 回忆内容，也可以在一次整理动作中把临时记忆提升为长期记忆；整理、修订、合并、拆分和遗忘都会产生明确事件。

The model may recall memory through the memory Skill and may explicitly promote temporary context during organization; organization, revision, merge, split, and forgetting produce explicit events.

## 模型画像 / Model Profiles

模型可以带有用户填写的初始描述。运行时保留这份先验，不让自动学习覆盖它；实际调用产生的可靠性、token、成本和延迟，以及用户对已完成运行给出的质量分，组成单独的学习画像。

A model may carry an initial user-authored description. Runtime preserves that prior instead of letting automatic learning overwrite it; observed reliability, tokens, cost, latency, and user quality scores for completed runs form a separate learned profile.

```python
profiles = user.models.list(purpose="code")
result = user.run("修复测试", purpose="code")
evaluation = user.models.evaluate_run(result.run_id, score=0.9)
```

表现按用户、Agent 和任务 `purpose` 隔离。少量质量样本通过置信度平滑，只逐步影响排序；成功返回本身不增加质量分。启用存储时，每个模型和 `purpose` 只保留一条可替换聚合状态，运行评价仍留在对应运行审计中。

Performance is isolated by user, Agent, and task `purpose`. Sparse quality samples are confidence-smoothed and influence ranking gradually; a successful response alone never raises quality. With storage enabled, each model and `purpose` keeps one replaceable aggregate state while the evaluation remains linked to its run audit.

## Skill 进化 / Skill Evolution

进化闭环不是“模型说改了就改”：

The evolution loop is not “the model says it changed, so it changes”:

1. 记录运行评价、成功状态、用量和替代调用证据。
2. 根据多维证据计算确定性的 Skill 保鲜度。
3. 创建候选 Skill 变更，但保持原版本有效。
4. 用声明的测试用例验证候选。
5. 通过后显式应用；需要时显式撤销。

1. Record evaluation, success, usage, and replacement-call evidence.
2. Calculate deterministic Skill freshness from multidimensional evidence.
3. Create a candidate change while keeping the current version active.
4. Test the candidate with declared cases.
5. Apply explicitly after passing, and undo explicitly when needed.

`SkillEvolution` 可以独立使用；`Agent.enable_skill_evolution()` 则把同一组动作作为渐进式工具交给模型。没有 `AgentLibrary` 时，启用进化会直接失败。

`SkillEvolution` can be used directly; `Agent.enable_skill_evolution()` exposes the same actions progressively to the model. Enabling evolution without an `AgentLibrary` fails directly.

## 外部授权 / External Authority

Skill 内容不能声明自己可更新。用户按插件或单个 Skill 明确授权：

Skill content cannot declare itself writable. The user grants authority per plugin or individual Skill:

```toml
[evolution]
allow = ["plugin:local/research", "skill:super-agent/common/review"]
auto_apply = ["plugin:local/research"]
```

`allow` 允许模型创建候选、执行测试和由调用方显式应用；`auto_apply` 必须是 `allow` 的子集，允许模型在所有声明测试通过后调用应用。两者都不绕过内容哈希前置条件。

`allow` permits candidate creation, testing, and caller-requested application. `auto_apply` must be a subset of `allow` and permits model-requested application only after every declared test passes. Neither bypasses content-hash preconditions.

代码中使用直白 API 获得相同行为：

The direct code APIs provide the same behavior:

```python
agent.enable_skill_evolution()
agent.allow_plugin_to_evolve("plugin:local/research")
agent.allow_skill_to_evolve(
    "skill:super-agent/common/review",
    auto_apply=False,
)
```

插件授权会展开为其引用的中央 Skill。共享只读 Skill 的更新写入当前用户和 Agent 的 Skill 覆盖层，并记录共享版本的 `base_hash`。基线已变化、读取哈希过期或测试失败时直接拒绝更新；不存在静默合并或偷偷退化。

Plugin authority expands to the central Skills it references. Updating a shared read-only Skill writes a user-Agent Skill overlay and records the shared revision as `base_hash`. A changed baseline, stale read hash, or failed test rejects the update; there is no silent merge or hidden degradation.

一次运行固定使用启动快照。即使模型在本轮应用了变更，本轮后续提示仍使用原 Skill；调用方刷新目录后，下一次顶层运行才加载新版本。

One run keeps its starting snapshot. Even when the model applies a change during that run, later turns in the same run retain the original Skill; after the caller refreshes the catalog, the next top-level run loads the new version.

每条 Skill 评价证据都关联原始运行 ID。记忆工具会把创建、提升和整理动作写入当前运行的紧凑审计事件，只保存哈希、大小、版本和 ID 等元数据；长期记忆正文仍由记忆状态单独管理。

Every Skill evaluation evidence item links to its originating run ID. Memory tools write creation, promotion, and organization as compact events in the current run, retaining only hashes, sizes, versions, and IDs; long-term memory text remains managed by the memory state separately.

## 保鲜度 / Freshness

保鲜度不依赖另一个模型。使用时间衰减、调用频率、成功分数、输入/输出 token、缓存读写和同类替代调用计算，可复现、可解释，也可以被新的评价证据更新。

Freshness does not require another model. It combines time decay, call frequency, success score, input/output tokens, cache reads/writes, and replacement calls in a reproducible and explainable calculation.
