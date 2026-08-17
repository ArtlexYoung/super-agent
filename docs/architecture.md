# 架构 / Architecture

## 五个边界 / Five Boundaries

```text
Provider  -> 提供模型智能 / provides model intelligence
Runtime   -> 调度一次运行 / schedules one run
Skill     -> 承载方法论和内容 / carries method and content
Agent     -> 组合模型、Skill、工具和子 Agent / composes everything
Adapter   -> 连接 CLI、存储和外部工具 / connects interfaces and effects
```

`Provider` 只负责把中立的 `ModelRequest` 转成模型事件。`Runtime` 只负责消息、工具、限制、事件和结束条件。

`Provider` converts a neutral `ModelRequest` into model events. `Runtime` owns messages, tools, limits, events, and termination.

`Skill` 是被动内容，不自行注册权限、密钥或 Python 代码。`Agent` 在代码中用 `add_subagent`、`add_skill_path`、`add_tool` 和 `add_model` 组合能力。

`Skill` is passive content and cannot register permissions, secrets, or Python code. `Agent` composes behavior in code with `add_subagent`, `add_skill_path`, `add_tool`, and `add_model`.

`Adapter` 不进入核心运行循环。没有使用数据库或进程工具时，核心仍可单独运行。

`Adapter` does not enter the core execution loop. The core remains usable without database or process adapters.

## 一条运行路径 / One Run Path

1. `Agent` 构造 `RunRequest`，附加用户作用域、模型目的和运行限制。
2. `Runtime` 创建 `RunSession`，通过中心 `SkillLibrary` 分页披露索引、正文和缓存路径。
3. `Provider` 流式返回文本、工具调用、用量和状态事件。
4. `Runtime` 显式执行已注册工具，继续下一轮或结束。
5. 事件监听器可把运行写入任意 `RecordBackend`，失败不会被伪造为成功。

1. `Agent` builds a `RunRequest` with user scope, model purpose, and run limits.
2. `Runtime` creates a `RunSession`; the central `SkillLibrary` discloses indexes, pages, and cache paths.
3. `Provider` streams text, tool calls, usage, and status events.
4. `Runtime` executes only registered tools and either continues or ends.
5. Listeners may write events to any `RecordBackend`; failures are never presented as success.

`RunSession` 只持有一个 `DisclosureStore`。Skill 正文和过大的文件、记忆、工具或子 Agent 结果都进入这一个存储，得到相同的哈希、缓存路径、偏移量和历史；没有各功能私有的截断规则。

Each `RunSession` owns one `DisclosureStore`. Skill bodies and large file, memory, tool, or subagent results enter that store and receive the same hashes, cache paths, offsets, and history; features do not own private truncation rules.

## Agent 树 / Agent Tree

`Agent.add_group` 创建不调用模型的结构组，`Agent.add_subagent` 或 `AgentGroup.add_subagent` 挂入可执行 Agent。根组是第 1 层，子层级由位置自动计算；一个 Agent 挂入时会保留已有子树。

`Agent.add_group` creates a model-free structural group, while `Agent.add_subagent` and `AgentGroup.add_subagent` attach executable Agents. The root group is level 1, child levels are derived from position, and an attached Agent keeps its existing subtree.

每个用户和根 Agent 只创建一个 `AgentTreeRuntime`。它统一保存任务、等待唤醒、共享板、决策、价格与权重选择、轮换、断路重试和动态记录压缩。同级组只通过父组共享板交换明确记录，正文进入同一个 `DisclosureStore`。

Each user and root Agent gets one `AgentTreeRuntime`. It owns tasks, sleep and wake events, shared boards, decisions, price and weight selection, rotation, circuit retries, and adaptive record compression. Sibling groups exchange explicit records only through their parent board, with bodies stored in the same `DisclosureStore`.

结构循环和多父挂载会成为带警告的委派链接，不会破坏树。`max_agent_level` 限制结构，`max_agent_call_depth` 限制实际递归调用；两者默认都不设上限。

Structural cycles and multiple parents become warned delegation links instead of corrupting the tree. `max_agent_level` limits structure, while `max_agent_call_depth` limits actual recursive calls; both are unlimited by default.

## 设计取舍 / Design Tradeoffs

- 不预设触发词，Skill 是否相关由模型结合索引判断。
- 不把记忆、会话或安全规则强绑定到核心。
- 不做隐式降级；Provider 路由的回退必须由 `RouterSettings` 明确允许。
- 读操作与写操作分开，写操作带有工具效果和确认策略。

- There is no trigger-word table; the model judges relevance from the index.
- Memory, conversations, and safety rules are not hard-wired into the core.
- There is no hidden fallback; Provider fallback must be enabled in `RouterSettings`.
- Reads and writes are separate, and writes carry tool effects and confirmation policy.
