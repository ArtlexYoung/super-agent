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
