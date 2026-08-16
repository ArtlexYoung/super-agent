# 后续方向 / Roadmap

v0.2.0 先固定最小边界：一个 Runtime、一条披露路径、一个事件契约和显式可选适配器。

v0.2.0 first fixes the minimum boundaries: one Runtime, one disclosure path, one event contract, and explicit optional adapters.

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
