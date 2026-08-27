# 运行时 / Runtime

## 中立模型接口 / Neutral Model Interface

Provider 接收 `ModelRequest`，返回 `ModelEvent`：文本增量、工具调用、用量和状态。Runtime 不知道具体供应商的 HTTP 格式。

A Provider receives `ModelRequest` and returns `ModelEvent` values for text deltas, tool calls, usage, and status. Runtime does not know a vendor's HTTP format.

模型请求带有 `purpose`、所需特性和元数据。`ModelRouter` 可以按目的、特性、权重、价格、调用可靠性和带置信度的显式质量评价选择模型；回退由设置显式控制，断路器打开时会返回可解释失败。

Requests carry a `purpose`, required features, and metadata. `ModelRouter` can select by purpose, features, weight, price, call reliability, and confidence-smoothed explicit quality evaluations; fallback is explicit, and an open circuit returns an explainable failure.

模型画像保留用户填写的 `description`，并单独附加学习出的表现摘要。自然语言描述不会被底层关键词表硬匹配；它通过 `Agent.list_model_profiles()` 和 Agent 树提供给上层模型或应用。确定性路由只使用结构化用途、特性、价格、权重和表现证据。

Model profiles preserve the user-authored `description` and append a separate learned performance summary. Natural-language descriptions are never matched by a hard-coded keyword table; `Agent.list_model_profiles()` and the Agent tree expose them to an upstream model or application. Deterministic routing uses only structured purpose, features, price, weight, and observed evidence.

调用成功或失败自动更新可靠性、token、成本和延迟，但不会被解释为回答质量。完成的运行可以通过 `agent.for_user("alice").models.evaluate_run(run_id, score=0.8)` 接收一次显式质量评价；重复同一评价是幂等的，冲突分数会失败。

Call success or failure automatically updates reliability, tokens, cost, and latency, but is never interpreted as answer quality. A completed run can receive one explicit quality evaluation through `agent.for_user("alice").models.evaluate_run(run_id, score=0.8)`; repeating the same evaluation is idempotent, while a conflicting score fails.

## 唯一运行循环 / One Execution Loop

```text
run.started
  -> model.call.started
  -> model.text.delta / model.tool.requested / model.usage
  -> tool.started -> tool.completed or tool.failed
  -> (next model turn)*
  -> run.completed or run.failed
```

同步和流式 API 不允许各自维护一套模型调用逻辑。工具输出、Skill 正文、记忆和子 Agent 摘要都进入同一 `RunContext` 上下文预算。

Synchronous and streaming APIs do not maintain separate model-call logic. Tool output, Skill bodies, memory, and subagent summaries share one `RunContext` context budget.

运行开始时，`AgentLibrary` 将 Skill、MCP 和插件引用图的版本与内容哈希固定为 `library_snapshot`，并写入运行开始、完成和结果元数据。运行中的更新不会改变这个快照，新内容只在下一次顶层运行读取。

At run start, `AgentLibrary` freezes Skill, MCP, and plugin-reference versions and content hashes as `library_snapshot`, recorded in start, completion, and result metadata. An update during the run cannot alter that snapshot; new content is read only by the next top-level run.

`RuntimeLifecycle` 位于核心运行循环中，记录父子运行、任务状态、深度和活动数量。多 Agent Skill 只把任务挂到当前 `RunContext` 的生命周期；子 Agent 继续使用同一对象，因此运行事件、任务等待和恢复元数据可以对齐，而不会保存模型正文。

`RuntimeLifecycle` lives in the core execution loop and records parent-child runs, task states, depth, and active counts. Multi-Agent Skills attach tasks to the current `RunContext` lifecycle; child Agents keep using that object, so run events, task waits, and recovery metadata stay aligned without storing model text.

自适应记录模式会先按任务位置给出计划，再检查实际子结果的字符数和事件数。超过 `max_full_result_characters` 或 `max_full_result_events` 时才压缩，并在结果中标明原因；`full` 和 `summary` 配置仍是明确的强制选择。

Adaptive recording first plans a mode from task position, then checks the actual child result's character and event counts. It compresses only after `max_full_result_characters` or `max_full_result_events` is exceeded, and records the reason; explicit `full` and `summary` modes remain authoritative.

## 身份与嵌套 / Identity and Nesting

`RunIdentity` 固定 `user_id`、`agent_name`、`run_id`、父运行和调用深度。`Agent.add_group` 和 `Agent.add_subagent` 在代码中组合树；根组是第 1 层，省略 Agent 名称时自动使用 `subagent01`、`subagent02` 等名称。

`RunIdentity` fixes `user_id`, `agent_name`, `run_id`, parent run, and call depth. `Agent.add_group` and `Agent.add_subagent` compose the tree in code; the root group is level 1, and omitted Agent names become `subagent01`, `subagent02`, and so on.

运行开始前只在树发生变化时检查层级、空组和循环委派。结构最大层级与实际调用最大深度分别配置；限制触发时直接失败，不在后台继续运行。

Before a run, structure is checked only after the tree changes. Maximum tree level and actual call depth are configured separately; reaching either explicit limit fails directly instead of continuing in the background.

## 批量派发 / Batch Dispatch

`dispatch_agent_tasks` 是组织树提供的通用原子派发机制。调用方先创建至少两个目标、目的和所需特性一致的任务，再一次性要求不同 Agent；需要模型多样性时还可以要求不同模型。完整分配成功后任务才并行启动。

`dispatch_agent_tasks` is the organization tree's generic atomic dispatch mechanism. A caller first creates at least two tasks with the same target, purpose, and required features, then requests distinct Agents in one call; distinct models may also be required. Tasks start in parallel only after the complete assignment succeeds.

Agent 或模型不足时，任务保持原状态并返回明确错误，不会部分启动。Runtime 只提供这一通用机制；独立检视、交叉验证和争议裁决等方法由 `skill:super-agent/common/review` 定义。

When Agent or model diversity is insufficient, tasks remain unchanged and an explicit error is returned. Runtime provides only this generic mechanism; `skill:super-agent/common/review` defines independent review, cross-checking, and dispute adjudication.

## 事件监听 / Event Listening

```python
events = []
agent.add_event_listener(events.append)
result = agent.run("任务")
```

监听器错误默认终止运行并保留原始错误；需要隔离观察器时，可在 `RunRequest` 中显式允许监听器错误。

Listener errors stop the run by default and preserve the original error; an observer can be isolated only by explicitly allowing listener failures in `RunRequest`.
