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

`SkillEvolution` 可以独立使用；`Agent.enable_skill_evolution()` 则把同一组动作作为渐进式工具交给模型。没有 Skill Library 时，启用进化会直接失败。

`SkillEvolution` can be used directly; `Agent.enable_skill_evolution()` exposes the same actions progressively to the model. Enabling evolution without a Skill Library fails directly.

## 保鲜度 / Freshness

保鲜度不依赖另一个模型。使用时间衰减、调用频率、成功分数、输入/输出 token、缓存读写和同类替代调用计算，可复现、可解释，也可以被新的评价证据更新。

Freshness does not require another model. It combines time decay, call frequency, success score, input/output tokens, cache reads/writes, and replacement calls in a reproducible and explainable calculation.
