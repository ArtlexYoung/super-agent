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

同步和流式 API 不各自维护模型调用逻辑。工具输出、Skill 正文、记忆和子 Agent 摘要都进入同一个 `ContextLedger` 预算。

Synchronous and streaming APIs do not maintain separate model-call logic. Tool output, Skill bodies, memory, and subagent summaries share one `ContextLedger` budget.

运行开始时，`AgentLibrary` 将 Skill、MCP 和插件引用图的版本与内容哈希固定为 `library_snapshot`，并写入运行开始、完成和结果元数据。运行中的更新不会改变这个快照，新内容只在下一次顶层运行读取。

At run start, `AgentLibrary` freezes Skill, MCP, and plugin-reference versions and content hashes as `library_snapshot`, recorded in start, completion, and result metadata. An update during the run cannot alter that snapshot; new content is read only by the next top-level run.

`RuntimeLifecycle` 位于核心运行循环中，记录父子运行、任务状态、深度和活动数量。多 Agent Skill 把任务挂到当前生命周期；子 Agent 继续使用同一对象，因此运行事件、任务等待和恢复元数据可以对齐，而不会保存模型正文。

`RuntimeLifecycle` lives in the core execution loop and records parent-child runs, task states, depth, and active counts. Multi-Agent Skills attach tasks to the current lifecycle; child Agents keep using that object, so run events, task waits, and recovery metadata stay aligned without storing model text.

自适应记录模式会先按任务位置给出计划，再检查实际子结果的字符数和事件数。超过 `max_full_result_characters` 或 `max_full_result_events` 时才压缩，并在结果中标明原因；`full` 和 `summary` 配置仍是明确的强制选择。

Adaptive recording first plans a mode from task position, then checks the actual child result's character and event counts. It compresses only after `max_full_result_characters` or `max_full_result_events` is exceeded, and records the reason; explicit `full` and `summary` modes remain authoritative.

## 执行故障边界 / Execution Fault Boundaries

默认可信 Python 工具仍在当前线程同步执行，不创建 worker。显式设置 `AgentContext(tool_timeout_seconds=...)` 后，工具由 daemon 线程执行；截止时间后 Runtime 返回控制，不等待线程结束。普通工具异常仍作为 `tool.failed` 交给模型；未确认的超时或运行中取消则抛出 `ToolOutcomeUnknown`（`FatalToolError`），记录 `outcome=unknown` 和 `execution_stopped` 并结束本次运行，不继续模型轮次。v0.2.124 将超时从可重试的普通工具错误收紧为这一失败契约。

Trusted Python tools still run synchronously in the calling thread by default, with no worker. Setting `AgentContext(tool_timeout_seconds=...)` explicitly enables a daemon worker; Runtime returns control at the deadline without waiting for that thread. Ordinary tool exceptions remain model-visible `tool.failed` results. Unconfirmed timeouts or cancellation during execution raise `ToolOutcomeUnknown` (a `FatalToolError`), record `outcome=unknown` and `execution_stopped`, and end the run before another model turn. v0.2.124 tightens the timeout contract from an ordinary retryable tool error to this failure.

同一 handler 在超时 worker 退出前不能再次执行；全进程最多有 64 个未退出的超时 worker，达到上限时新工具不启动。迟到结果不交付，过期工具上下文拒绝新事件。Python 线程不能强制终止，仍可能修改已持有的可变对象或产生外部效果；同进程工具也不隔离原生崩溃、`os._exit` 或持有 GIL 的代码。需要硬停止的服务通过可信代码显式使用独立 stdio MCP 进程。

The same handler cannot run again until an abandoned worker exits. At most 64 timed workers may remain active in the process; capacity exhaustion rejects new timed calls before dispatch. Late results are not delivered, and expired tool contexts reject new events. Python threads cannot be forcibly terminated and may still mutate objects or produce external effects. In-process tools do not isolate native crashes, `os._exit`, or code that holds the GIL. Services requiring hard stop use an independent stdio MCP process explicitly configured by trusted host code.

`StdioMcpServer` 的锁等待、初始化、写入、通知和响应读取共用一次 monotonic 截止时间。管道非阻塞，`max_message_bytes`（默认 1,000,000）限制请求和接收缓冲；stderr 排空但不保留。半条 JSON、连续通知、阻塞 stdin 或进程退出都会结束等待。故障回收只作用于本实例启动的进程组：TERM 等待 0.2 秒，再 KILL 等待 0.8 秒，超时后可能增加最多 1 秒的本地回收等待；这不是操作系统实时保证。`close()` 会通知正在等待的请求退出，锁等待上限 2 秒。回收失败保留故障对象并如实报告，后续请求不会复用旧缓冲或自动重放。

`StdioMcpServer` shares one monotonic deadline across lock acquisition, initialization, writes, notifications, and response reads. Nonblocking pipes and `max_message_bytes` (1,000,000 by default) bound requests and receive buffers; stderr is drained without retention. Partial JSON, endless notifications, blocked stdin, and worker exits all end the wait. Cleanup affects only this instance's process group: TERM waits 0.2 seconds, then KILL waits 0.8 seconds, adding at most one second of local cleanup waits after a timeout; this is not an operating-system realtime guarantee. `close()` interrupts the waiting request and bounds lock acquisition to two seconds. Failed cleanup retains fault state; later requests never reuse stale buffers or replay automatically.

`tools/call` 可能已经发出而未收到有效响应时，MCP 保留 `unknown_operation_id`，阻止所有后续工具调用；仍可通过 `list_tools()` 重启并检查服务。宿主核对外部效果后，使用对应 ID 调用 `resolve_unknown(id)` 才解除阻止。确认进程停止不等于撤销副作用，也不等于得到工具结果。stdio 的未知调用记录只存在于当前对象；需要跨宿主进程恢复时，沿用已有显式任务/操作持久化记录，不依赖重新创建 MCP 对象。

When a `tools/call` may have been sent without a valid response, MCP retains `unknown_operation_id` and blocks subsequent calls. `list_tools()` can still restart and inspect the service. The host reconciles external effects and then calls `resolve_unknown(id)` with the matching ID. Confirming process termination neither rolls back effects nor supplies a result. This stdio latch lives only in the current object; recovery across host processes uses existing explicit task/operation persistence instead of relying on a new MCP object.

TaskQueue 将这类失败保留为 `failed / stop_unknown`，优先于取消确认和临时错误回退处理。恢复与退回都不能把未知尝试变成可运行任务；核对外部效果后，由宿主显式创建新任务。权限拒绝、关键记录读写失败与产物验收失败的既有语义保持。

TaskQueue retains these failures as `failed / stop_unknown`, before cancellation confirmation or transient-error fallback. Restore and return-for-changes cannot turn an unknown attempt into runnable work. After reconciliation the host explicitly creates a new task. Existing permission-denial, critical-record, and artifact-acceptance failure semantics remain intact.

```python
from super_agent.adapter.process import StdioMcpServer
from super_agent.core.run import ToolOutcomeUnknown

server = StdioMcpServer(["python3", "research_service.py"], timeout_seconds=10)
try:
    result = server.call_tool("run_experiment", {"dataset": "sample.csv"})
except ToolOutcomeUnknown as error:
    # 先核对实验、文件或其他外部效果，不能自动重试。
    operation_id = error.operation_id
    # 核对完成并确认旧进程已停后：server.resolve_unknown(operation_id)
finally:
    server.close()
```

## 按需服务与状态 / On-Demand Services and Status

v0.2.126 中，`Agent.connect_mcp_server(...)` 只绑定宿主提供的对象和副作用声明。每次运行冻结绑定对象、effects 和资源快照；只有宿主指定相关 plugin 或模型调用 `activate_plugin` 时，才列举该方法引用的服务。普通任务不探测未选中的服务，同一运行内成功解析的工具只列举一次。required 缺失或服务故障会使激活失败；未绑定的 optional 仍报告 unavailable，已绑定但故障的 optional 明确失败。显式指定 plugin 时在首次模型调用前报告，模型发起的激活失败沿用工具错误与原子回滚边界。

In v0.2.126, `Agent.connect_mcp_server(...)` only binds the host-provided object and effect declarations. Each run freezes the binding objects, effects and resource snapshot; service tools are listed only when the host selects a related plugin or the model calls `activate_plugin`. Ordinary tasks leave unselected services untouched, and successfully resolved tools are listed once per run. Missing required bindings and service failures reject activation. Unbound optional services remain unavailable; bound optional services that fail reject activation. Explicit plugin selection reports failure before the first model call; model-triggered activation uses the existing tool-error and atomic rollback boundaries.

宿主直接调用 `StdioMcpServer.service_status()` 获取独立字典副本；查询不启动进程、不发送 MCP 请求、不调用模型、不写盘。读取非阻塞：请求占用锁时，返回最近观察并标 `busy=true`，因此不能把它当作实时保证或工具授权。正常成功后仍保留最近故障供核对。

The host calls `StdioMcpServer.service_status()` for a detached dictionary snapshot. It starts no process, sends no MCP request, calls no model and writes no files. Reads are nonblocking: when a request holds the lock, they return the latest observation with `busy=true`, so the snapshot is neither a realtime guarantee nor permission to execute. Successful requests retain the last fault for inspection.

| 字段 / Field | 含义 / Meaning |
| --- | --- |
| `phase` | `unstarted` / `running` / `exited` / `stop_unconfirmed`；进程运行不证明初始化或清理成功 / A running process alone proves neither initialization nor cleanup success |
| `generation` | 当前对象每次成功创建进程时递增，从 0 开始 / Incremented for each process creation in this object, starting at 0 |
| `admission` | `allowed` / `blocked_unknown` / `unavailable`；调用仍检查权限与当前事实 / Calls still check permissions and current facts |
| `pending_operation` | `present`；存在时附 `operation_id` 和该失联调用的 `execution_stopped` 证据 / Presence, operation ID and stop evidence for the lost call |
| `last_fault` | `present`；存在时附最多 128 字符的 `error_type` 与故障发生时的 `generation`，不返回错误正文 / Presence, bounded error type and generation at failure, without error text |
| `observed_at`, `busy` | 本进程 monotonic 观察时间及锁是否忙，时间不能跨进程比较 / Process-local monotonic observation time and lock occupancy; timestamps are not comparable across processes |

从仓库根目录可运行下面的本地协议示例；fixture 是测试进程，真实应用将命令替换为自己的服务并由宿主负责关闭：

Run this local protocol example from the repository root. The fixture is a test process; applications supply their own service command and retain responsibility for closing it:

```python
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from super_agent.adapter.process import StdioMcpServer

fixture = Path("tests/fixtures/mcp_fault_worker.py").resolve()
with TemporaryDirectory() as directory:
    Path(directory, "settings.json").write_text("{}", encoding="utf-8")
    server = StdioMcpServer(
        [sys.executable, "-I", "-B", "-u", str(fixture), directory],
        root=directory, timeout_seconds=2,
    )
    try:
        assert server.service_status()["phase"] == "unstarted"
        server.list_tools()  # Explicitly start and inspect the service.
        generation = server.service_status()["generation"]
        assert server.service_status()["admission"] == "allowed"
        server.close()
        assert server.service_status()["phase"] == "exited"
        server.list_tools()  # Explicitly establish a new generation.
        assert server.service_status()["generation"] == generation + 1
    finally:
        server.close()
```

`mcp_tools(...)` 对自带 stdio 同时捕获工具描述与进程代次，派发前在同一请求锁内核对。进程退出或重建后，旧 Tool 抛出 `FatalToolError`，不会自动启动新进程执行旧 schema；Agent 在下一次运行重新激活方法，直接宿主则重新调用 `mcp_tools(...)` 获取新工具。即使进程在核对后立即退出，也只尝试原进程，不切换到新进程。旧 `list_tools()` / `call_tool(name, arguments)` 签名及第三方两方法 McpServer 协议保持兼容；第三方对象没有本适配器的代次保证。

For the built-in stdio adapter, `mcp_tools(...)` captures tool descriptions and process generation together, then checks the binding under the same request lock before dispatch. Exited or rebuilt processes cause old Tools to raise `FatalToolError`; they never start a new process to execute an old schema. Agents reactivate the method in a new run; direct hosts call `mcp_tools(...)` again for fresh tools. A process that exits immediately after validation is still the only process attempted. Existing `list_tools()` / `call_tool(name, arguments)` signatures and the third-party two-method McpServer protocol remain compatible; third-party objects do not acquire this generation guarantee.

`close()` 和 `list_tools()` 不解除 unknown；服务重新可用后仍需核对外部效果并调用 `resolve_unknown(匹配 ID)`。该操作只解除当前对象的调用阻止，不认可结果、不修改 TaskQueue 的旧 `stop_unknown` 历史。`Agent.close()` 不关闭借用的服务。CLI `check` 仍只读配置和被动资源，不连接或探测服务。

Neither `close()` nor `list_tools()` clears unknown outcomes. Even after the service becomes available, the host must reconcile external effects and call `resolve_unknown(matching_id)`. This only clears the current object's call gate: it supplies no accepted result and does not change old TaskQueue `stop_unknown` history. `Agent.close()` does not close borrowed services. CLI `check` still reads configuration and passive resources without connecting to or probing services.

## 长会话窗口 / Long-Running Windows

`TokenUsage` 统一供应商准确用量和显式标记的估算用量。启用 `context-window` 插件并声明模型容量后，`TokenBudget` 只在模型调用前请求换窗；未知容量不会猜测，换窗仍无法容纳请求时会明确失败。

`TokenUsage` normalizes exact provider usage and explicitly marked estimates. After enabling the `context-window` plugin and declaring model capacity, `TokenBudget` requests rollover only before a model call; unknown capacity is never guessed, and a request that still does not fit fails visibly.

关闭窗口的正文使用同一个 `ResourceCenter` 内容寻址缓存，运行结果只保存历史引用和元数据。调用方可以显式恢复或分叉历史窗口；`context-compression` 是独立可选插件，只有模型调用工具并给出摘要时才压缩，不自动总结或静默删除上下文。

Closed-window content uses the same content-addressed `ResourceCenter`, while run results retain only history references and metadata. Callers may explicitly restore or fork a prior window; `context-compression` is a separate optional plugin and compresses only when the model calls its tool with a summary, never through automatic summarization or silent deletion.

运行检查点使用单调事件序号，旧版本不能覆盖新状态，同序号不同内容会报告冲突。记忆检查点只保留记忆 ID、会话 ID 和时间；创建是显式写操作，列出检查点不会读取或复制记忆正文。

Run checkpoints use monotonic event sequences: stale versions cannot replace newer state, and different content at the same sequence reports a conflict. Memory checkpoints retain only memory IDs, session IDs, and timestamps; creation is an explicit write, while listing never reads or copies memory text.

## 身份与嵌套 / Identity and Nesting

`RunIdentity` 固定 `user_id`、`agent_name`、`run_id`、父运行和调用深度。`Agent.add_group` 和 `Agent.add_subagent` 在代码中组合树；根组是第 1 层，省略 Agent 名称时自动使用 `subagent01`、`subagent02` 等名称。

`RunIdentity` fixes `user_id`, `agent_name`, `run_id`, parent run, and call depth. `Agent.add_group` and `Agent.add_subagent` compose the tree in code; the root group is level 1, and omitted Agent names become `subagent01`, `subagent02`, and so on.

运行开始前只在树发生变化时检查层级、空组和循环委派。结构最大层级与实际调用最大深度分别配置；限制触发时直接失败，不在后台继续运行。

Before a run, structure is checked only after the tree changes. Maximum tree level and actual call depth are configured separately; reaching either explicit limit fails directly instead of continuing in the background.

## 批量派发 / Batch Dispatch

`dispatch_agent_tasks` 是组织树提供的通用原子派发机制。调用方先创建至少两个目标、目的和所需特性一致的任务，再一次性要求不同 Agent；需要模型多样性时还可以要求不同模型。完整分配成功后任务才并行启动。

`dispatch_agent_tasks` is the organization tree's generic atomic dispatch mechanism. A caller first creates at least two tasks with the same target, purpose, and required features, then requests distinct Agents in one call; distinct models may also be required. Tasks start in parallel only after the complete assignment succeeds.

Agent 或模型不足时，任务保持原状态并返回明确错误，不会部分启动。Runtime 只提供这一通用机制；独立检视、交叉验证和争议裁决等方法由 `skill:super-agent/review/multi-agent` 定义。

When Agent or model diversity is insufficient, tasks remain unchanged and an explicit error is returned. Runtime provides only this generic mechanism; `skill:super-agent/review/multi-agent` defines independent review, cross-checking, and dispute adjudication.

## 事件监听 / Event Listening

```python
events = []
agent.add_event_listener(events.append)
result = agent.run("任务")
```

监听器错误默认终止运行并保留原始错误；直接调用 Runtime 且需要隔离观察器时，可在 `RunInput` 中显式设置 `allow_listener_failures=True`。

Listener errors stop the run by default and preserve the original error. Direct Runtime callers can isolate observers explicitly with `RunInput(allow_listener_failures=True)`.
