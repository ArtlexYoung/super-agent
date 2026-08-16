# 运行时 / Runtime

## 中立模型接口 / Neutral Model Interface

Provider 接收 `ModelRequest`，返回 `ModelEvent`：文本增量、工具调用、用量和状态。Runtime 不知道具体供应商的 HTTP 格式。

A Provider receives `ModelRequest` and returns `ModelEvent` values for text deltas, tool calls, usage, and status. Runtime does not know a vendor's HTTP format.

模型请求带有 `purpose`、所需特性和元数据。`ModelRouter` 可以按目的、特性、权重、价格和健康度选择模型；回退由设置显式控制，断路器打开时会返回可解释失败。

Requests carry a `purpose`, required features, and metadata. `ModelRouter` can select by purpose, features, weight, price, and health; fallback is explicit, and an open circuit returns an explainable failure.

## 唯一运行循环 / One Execution Loop

```text
run.started
  -> model.call.started
  -> model.text.delta / model.tool.requested / model.usage
  -> tool.started -> tool.completed or tool.failed
  -> (next model turn)*
  -> run.completed or run.failed
```

同步和流式 API 不允许各自维护一套模型调用逻辑。工具输出、Skill 正文、记忆和子 Agent 摘要都进入同一 `RunSession` 上下文预算。

Synchronous and streaming APIs do not maintain separate model-call logic. Tool output, Skill bodies, memory, and subagent summaries share one `RunSession` context budget.

## 身份与嵌套 / Identity and Nesting

`RunIdentity` 固定 `user_id`、`agent_name`、`run_id`、父运行和深度。`Agent.add_subagent` 在代码中组合层级；省略名称时自动使用 `subagent01`、`subagent02` 等名称。

`RunIdentity` fixes `user_id`, `agent_name`, `run_id`, parent run, and depth. `Agent.add_subagent` composes the tree in code; omitted names become `subagent01`, `subagent02`, and so on.

运行时只提醒深度过深或检测到循环链路；最大深度是可选配置。限制触发时直接失败，不在后台继续运行。

Runtime warns about deep nesting or cycle paths; maximum depth is optional. When a limit is reached, the run fails directly instead of continuing in the background.

## 事件监听 / Event Listening

```python
events = []
agent.add_event_listener(events.append)
result = agent.run("任务")
```

监听器错误默认终止运行并保留原始错误；需要隔离观察器时，可在 `RunRequest` 中显式允许监听器错误。

Listener errors stop the run by default and preserve the original error; an observer can be isolated only by explicitly allowing listener failures in `RunRequest`.
