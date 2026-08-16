# AG-UI 适配 / AG-UI Adapter

AG-UI 是可选的外部事件协议，不是核心运行时接口。核心只产生 `RunEvent`，`adapter/http/agui.py` 再把它们映射成 SSE/AG-UI 事件。

AG-UI is an optional external event protocol, not the core runtime interface. Core emits `RunEvent` values; `adapter/http/agui.py` maps them to SSE/AG-UI events.

## 输入 / Input

适配器读取 `threadId`、`runId`、`messages` 和可选 `forwardedProps.skill`。正文取最后一条用户消息，Skill 名称只是一次运行的显式限制，不是触发词。

The adapter reads `threadId`, `runId`, `messages`, and optional `forwardedProps.skill`. The prompt is the latest user message; a Skill name is an explicit per-run restriction, not a trigger word.

## 输出 / Output

运行开始、文本增量、工具结果、运行完成或失败会映射为有序事件；同时保留 `CUSTOM` 事件，携带脱敏后的原始 Runtime 事件类型和序号。

Run start, text deltas, tool results, and run finish or failure become ordered events; `CUSTOM` events preserve the redacted Runtime event type and sequence.

```python
from adapter.http.agui import AGUIEventMapper

mapper = AGUIEventMapper("thread", "run")
for event in agent.stream("hello"):
    for message in mapper.map_runtime_event(event):
        send_sse(message)
```

任何客户端都可以替换 AG-UI，只要它消费 `RunEvent` 或实现自己的 Adapter；核心不依赖 AG-UI 包。

Any client may replace AG-UI by consuming `RunEvent` or implementing another adapter; the core does not depend on an AG-UI package.
