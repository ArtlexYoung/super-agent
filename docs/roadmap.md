# 后续方向 / Roadmap

v0.2.0 先固定最小边界：一个 Runtime、一条披露路径、一个事件契约和显式可选适配器。

v0.2.0 first fixes the minimum boundaries: one Runtime, one disclosure path, one event contract, and explicit optional adapters.

到 v0.2.67，多 Agent 已收成一棵代码定义的组树；运行输入、资源披露、记录、任务状态、候选选择和存储转换也分别只有一个实现。

By v0.2.67, multi-Agent work uses one code-defined group tree, while run input, resource disclosure, records, task state, candidate selection, and storage conversion each have one implementation.

v0.2.67 将事件类别和关键日志判定收敛到 `core.event`，Runtime、记录和适配器共享同一事件视图。

v0.2.67 centralizes event categories and critical-log classification in `core.event`, so Runtime, records, and adapters share one event view.

v0.2.67 增加只读 Agent 组合快照，外部界面通过公开视图读取状态，不依赖内部字段。

v0.2.67 adds a read-only Agent assembly snapshot so external interfaces use a public view instead of internal fields.

v0.2.67 将 Runtime 的开始、检查点、完成事件和结果统一为同一份元数据快照。

v0.2.67 uses one metadata snapshot for Runtime start, checkpoints, completion events, and results.

v0.2.67 让单条记录和记录列表共享统一的审计投影。

v0.2.67 makes single-record and record-list inspection share one audit projection.

v0.2.67 增加显式的单任务读取与等待接口，统一计划、队列和子 Agent 的状态观察。

v0.2.67 adds explicit single-task read and wait APIs for one shared view across plans, queues, and subagents.

v0.2.67 为模型路由提供不调用模型的选择解释和成本估算。

v0.2.67 adds model-routing explanations and cost estimates without calling a model.

v0.2.67 将副作用动作显式化，模型请求本身不再等同于已经执行。

v0.2.67 makes side-effect actions explicit so a model request is never treated as execution.

v0.2.67 增加临时记忆清理动作，确保短期上下文可以显式结束而不会影响长期经验。

v0.2.67 adds an explicit temporary-memory cleanup action so short-term context can end without changing durable experience.

v0.2.67 为 Skill 进化提供只读事务列表和状态查询。

v0.2.67 adds read-only Skill evolution transaction lists and status queries.

v0.2.67 为旁观复核提供独立结果收集，多样性不足时明确失败。

v0.2.67 adds independent result collection for external review and fails explicitly when diversity is insufficient.

v0.2.68 提供不绑定协议的 Agent 状态和运行追踪查询，界面只消费 JSON 视图。

v0.2.68 provides protocol-neutral Agent state and run-trace queries, with interfaces consuming JSON views only.

v0.2.69 将用户、工作目录、Agent 和流的隔离语义统一到全部存储后端。

v0.2.69 gives every storage backend the same isolation semantics for users, working directories, Agents, and streams.

v0.2.70 用真实资源快照固定插件清单、引用、版本与去重发布契约。

v0.2.70 fixes plugin manifest, reference, version, and deduplication release contracts through the real resource snapshot.

v0.2.71 增加通用 Runtime 扩展契约。扩展只能通过模型调用前后的只读视图参与运行，当前版本不改变普通 Agent 的单窗口行为。

v0.2.71 adds the generic Runtime extension contract. Extensions participate only through read-only views before and after model calls; the current version does not change the single-window behavior of a plain Agent.

v0.2.72 增加插件到 Runtime 扩展的显式注册边界。插件清单只能声明已注册扩展名称，不能通过配置导入或执行任意代码。

v0.2.72 adds the explicit registration boundary between plugins and Runtime extensions. A plugin manifest may declare only a registered extension name and cannot import or execute arbitrary code through configuration.

v0.2.73 统一供应商准确 token 用量和带来源标记的估算用量，并让模型容量进入路由画像。

v0.2.73 unifies exact provider token usage and source-marked estimates, and exposes model capacity through routing profiles.

v0.2.74 为一次运行增加可观察的模型窗口生命周期。

v0.2.74 adds observable model-window lifecycles to each run.

v0.2.75 只在模型调用前安全换窗，不重放已经完成的工具副作用。

v0.2.75 rolls windows over only before model calls and never replays completed tool side effects.

v0.2.76 用可选 `context-window` 插件和明确模型容量控制 token 预算；未知容量不猜测。

v0.2.76 controls token budgets through the optional `context-window` plugin and declared model capacity, without guessing unknown limits.

v0.2.77 将窗口正文接入中央内容寻址缓存，只在渐进式历史索引中保留引用和元数据。

v0.2.77 stores window bodies in the central content-addressed cache and retains only references and metadata in the progressive history index.

v0.2.78 支持显式恢复或分叉历史窗口，不修改原始缓存。

v0.2.78 supports explicit history-window restoration and forking without changing the original cache.

v0.2.79 增加独立 `context-compression` 插件；只有模型显式给出摘要时才压缩上下文。

v0.2.79 adds the independent `context-compression` plugin, which compresses context only from a model-supplied explicit summary.

v0.2.80 使用单调检查点防止旧运行覆盖新状态，增加不复制正文的记忆检查点，并将日志清理固定在用户、Agent 和工作目录作用域。

v0.2.80 uses monotonic checkpoints to prevent stale run overwrites, adds memory checkpoints without copied text, and fixes log cleanup to user, Agent, and working-directory scope.

v0.2.81 让默认 CLI 连续聊天在内存中携带前序用户消息与助手回答，保持单一历史来源。

v0.2.81 lets the default CLI chat carry prior user messages and assistant answers in memory, keeping one history source.

v0.2.82 让 `--save` 自动创建会话并显示 ID，保存与恢复共用存储作为唯一历史来源。

v0.2.82 makes `--save` auto-create a conversation and show its ID, with save and restore sharing storage as the single history source.

v0.2.83 统一模型配置诊断：`check` 逐模型报告密钥状态与下一步，不泄露密钥，也不静默切换 Provider。

v0.2.83 unifies model-configuration diagnostics: `check` reports per-model credential status and the next step, without leaking secrets or silently switching Provider.

v0.2.84 按项目标记探测候选验证命令；候选仍走命令白名单与逐次确认，并返回 argv、cwd 与退出状态。

v0.2.84 probes candidate verification commands by project markers; candidates still pass the command whitelist and per-action confirmation, and return argv, cwd, and exit status.

v0.2.85 让 CLI 直接安装、打包、列出、读取与移除本地 Skill，复用 AgentLibrary 的冲突检查与回滚，并修复同内容重装的幂等哈希口径。

v0.2.85 lets the CLI install, pack, list, read, and remove local Skills directly, reusing AgentLibrary conflict checks and rollback, and fixes the idempotent content-hash basis for same-content reinstalls.

v0.2.86 统一到 `super_agent.*` 命名空间；在仓库外安装 wheel 可 `import super_agent`，并与宿主自己的 `core`/`skill` 包共存。

v0.2.86 unifies the codebase under the `super_agent.*` namespace; a wheel installed outside the repo supports `import super_agent` and coexists with a host project's own `core`/`skill` packages.

v0.2.87 让基础 Agent 运行只载入必要模块：未启用的团队、存储与 MCP 实现不再被普通运行导入；启用这些能力时仍正常加载与工作。

v0.2.87 makes a base Agent run load only necessary modules: unenabled team, storage, and MCP implementations are no longer imported by a plain run, while they still load and work when enabled.

v0.2.88 收敛 Agent 公共入口：不再复制死配置，模型画像归领域对象，构造、run/stream、工具与子 Agent 组合是唯一面向初学者的入口。

v0.2.88 converges the Agent public entry: it no longer duplicates dead configuration, model profiles belong to domain objects, and construction, run/stream, tools, and subagent composition are the single beginner-facing entry.

后续演进按风险和可验证性推进：

Future work should proceed by risk and verifiability:

- 用更多真实 Provider 和工具回放测试验证事件契约，而不是增加第二套执行循环。
- 为 Skill 候选变更补充隔离执行器和更细的测试报告，但保持 `propose -> test -> apply -> undo` 显式。
- 在不强绑定存储的前提下增加并发任务恢复、休眠与唤醒的可观察事件。
- 继续缩短公开入口，优先改善 `Agent()`、CLI 即开即用和错误信息。
- 只有在重复逻辑被真实测量后才合并抽象；不为未来场景预先增加配置项。

- Add replay tests with more real Providers and tools instead of adding a second execution loop.
- Add isolated candidate execution and richer reports while keeping `propose -> test -> apply -> undo` explicit.
- Add observable sleep, wake, and task-recovery events without binding storage.
- Keep shortening the public path, prioritizing `Agent()`, instant CLI use, and error messages.
- Merge abstractions only after repeated logic is measured; do not pre-add configuration for hypothetical scenarios.
