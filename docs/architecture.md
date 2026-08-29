# 架构 / Architecture

## 七个边界 / Seven Boundaries

```text
Provider -> 提供模型智能 / provides model intelligence
Runtime  -> 调度一次运行 / schedules one run
Library  -> 统一索引、去重、快照和写入边界 / unifies indexes, deduplication, snapshots, and writes
Plugin   -> 组合 Skill、MCP 和插件引用 / groups Skill, MCP, and plugin references
Skill    -> 承载被动方法与内容 / carries passive methods and content
Agent    -> 组合模型、插件、工具、状态和子 Agent / composes everything
Adapter  -> 连接 CLI、存储、进程和外部协议 / connects interfaces and effects
```

`Provider` 只把中立 `ModelRequest` 转成模型事件。`Runtime` 只管理消息、工具调用、限制、事件和结束条件。

`Provider` only converts a neutral `ModelRequest` into model events. `Runtime` only manages messages, tool calls, limits, events, and termination.

`Plugin` 是被动引用清单，不拥有 Skill 或 MCP，也不是可执行扩展。`Skill` 和 MCP 描述不能自行注册 Python 代码、连接、密钥、权限或进化授权；可信工具仍由 `Agent.add_tool`、`Agent.add_tools_for_skills` 或 `Agent.connect_mcp_server` 显式提供。

A `Plugin` is a passive reference list, not an executable extension. Skills and MCP definitions cannot register Python code, connections, secrets, permissions, or evolution authority; trusted tools remain explicit through `Agent.add_tool`, `Agent.add_tools_for_skills`, or `Agent.connect_mcp_server`.

`Agent` 使用 `add_library_path`、`enable_plugin`、`enable_skill`、`add_subagent` 和 `add_model` 组合行为。`Adapter` 不进入核心运行循环；没有 CLI、数据库或进程工具时，核心仍可独立运行。

`Agent` composes behavior with `add_library_path`, `enable_plugin`, `enable_skill`, `add_subagent`, and `add_model`. Adapters stay outside the core loop, which remains usable without a CLI, database, or process tools.

## 一条运行路径 / One Run Path

1. `Agent` 固定用户、Agent、工作目录和父运行身份，构造 `RunRequest`。
2. `AgentLibrary` 为该作用域建立不可变快照，并提供有界插件、Skill 与 MCP 索引。
3. 模型按语义选择内容，通过同一个 `DisclosureStore` 分页读取，再显式激活插件或 Skill。
4. `Provider` 流式返回文本、工具调用、用量和状态，`Runtime` 只执行已注册工具。
5. 结果和事件可以写入任意 `RecordBackend`；失败保留原始语义。

1. `Agent` fixes user, Agent, working-directory, and parent-run identity, then builds a `RunRequest`.
2. `AgentLibrary` creates an immutable scoped snapshot and bounded plugin, Skill, and MCP indexes.
3. The model selects content semantically, pages it through the shared `DisclosureStore`, and explicitly activates a plugin or Skill.
4. The Provider streams text, tool calls, usage, and status while Runtime executes only registered tools.
5. Results and events may be written to any `RecordBackend`; failures retain their original meaning.

一次顶层运行只看启动时的资源快照。运行中创建、更新或优化 Skill 不会改变当前提示；调用方显式刷新后，下一次运行才读取新快照。

A top-level run sees only the resource snapshot created at startup. Creating or updating a Skill cannot alter the current prompt; after an explicit refresh, the next run reads a new snapshot.

## 去重边界 / Deduplication Boundary

Skill、MCP 和插件分别使用 `skill:<id>`、`mcp:<id>` 和 `plugin:<id>`。同类型、同 ID、同版本和同哈希的资源跨多个根目录只形成一个逻辑对象并记录所有来源；相同身份但版本或内容不同会直接失败。

Skills, MCP definitions, and plugins use `skill:<id>`, `mcp:<id>`, and `plugin:<id>`. Resources of one type with matching ID, version, and hash across roots become one logical object with all sources recorded; divergent versions or content under one identity fail directly.

用户和 Agent 的可写 Skill 覆盖层必须声明原共享内容的 `base_hash`。只有基线仍一致时覆盖才生效，因此不会把并发变化静默合并。披露缓存按正文 SHA-256 物理去重，审计历史仍保留每个逻辑引用。

A user-Agent writable Skill overlay must declare the shared content's `base_hash`. It applies only while that baseline still matches, preventing silent merges of concurrent changes. Disclosure storage deduplicates bodies by SHA-256 while audit history preserves each logical reference.

## Agent 树 / Agent Tree

`Agent.add_group` 创建不调用模型的结构组，`Agent.add_subagent` 或 `AgentGroup.add_subagent` 挂入可执行 Agent。每个 Agent 可以选择自己的插件和 Skill，因此同一棵树可以自然形成专业分工。

`Agent.add_group` creates a model-free structural group, while `Agent.add_subagent` and `AgentGroup.add_subagent` attach executable Agents. Every Agent chooses its own plugins and Skills, allowing specialization within one tree.

每个用户和根 Agent 只有一个 `AgentTreeRuntime`。它统一保存任务、等待唤醒、共享板、分阶段决策、价格与权重选择、轮换、断路重试和动态记录压缩；没有子 Agent 时不会创建这些状态。

Each user and root Agent has one `AgentTreeRuntime`. It owns tasks, sleep and wake events, shared boards, staged decisions, price and weight selection, rotation, circuit retries, and adaptive record compression; none of this state exists without subagents.

结构循环和多父挂载会成为带警告的委派链接。`max_agent_level` 限制结构，`max_agent_call_depth` 限制实际递归调用；省略时均为无限。

Structural cycles and multiple parents become warned delegation links. `max_agent_level` limits structure and `max_agent_call_depth` limits actual recursive calls; both are unlimited when omitted.

## 设计取舍 / Design Tradeoffs

- 没有触发词表，相关性由模型结合索引判断。
- 记忆、会话、存储、安全策略、插件和多 Agent 都是可选组合。
- 读取与写入分开，写入使用哈希前置条件和显式权限。
- 不做隐藏回退；模型、权限、依赖或验证不足时明确失败。

- There is no trigger-word table; the model judges relevance from indexes.
- Memory, conversations, storage, safety policy, plugins, and multi-Agent behavior are optional composition.
- Reads and writes remain separate, with hash preconditions and explicit authority for writes.
- There is no hidden fallback; missing models, authority, dependencies, or evidence fail explicitly.
