# 发布 / Releasing

## 本地检查 / Local Checks

使用 Python 3.11 和 `uv`：

Use Python 3.11 and `uv`:

```bash
PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 \
python3.11 -m unittest discover -s tests -p 'test_*.py' -v

PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 \
python3.11 scripts/verify_release.py --version 0.2.126 --full
```

发布检查验证版本、Python 最低版本、零默认依赖、源码布局、旧目录删除、构建范围、内置插件去重关系和离线评测资产。

The release gate checks version, Python minimum, zero default dependencies, source layout, removed legacy directories, build scope, builtin plugin deduplication, and offline evaluation assets.

日志写入不会触发保留清理。先运行 `super-agent data storage prune --config common.toml --user alice` 预览，确认后追加 `--apply` 才删除当前用户、Agent 和工作目录中的到期日志并记录清理事件；状态流不会因为期限被删除。

Log writes never trigger retention cleanup. Run `super-agent data storage prune --config common.toml --user alice` to preview, then add `--apply` to delete expired logs in the current user, Agent, and working-directory scope and record the cleanup; state streams are not expired by this policy.

## 版本 / Version

v0.2.126 将 MCP 工具解析移至相关方法激活：未选中服务的故障不阻断普通任务，成功解析在本次运行内复用，绑定对象、schema 和 effects 保持冻结。自带 stdio 提供不启动服务的只读 service_status；描述与进程代次同时捕获，旧工具在退出或重建后派发前拒绝，不隐式换进程。unknown 仍需宿主显式核对，直接静态 RunInput、第三方两方法协议和借用服务所有权保持兼容。

v0.2.126 moves MCP tool resolution to activation of the relevant method: failures in unselected services no longer block ordinary tasks, successful resolutions are reused within the run, and binding objects, schemas and effects remain frozen. The built-in stdio adapter provides read-only service_status without starting a service; descriptions and process generation are captured together, and old tools reject dispatch after exit or replacement without implicitly switching processes. Unknown outcomes still require explicit host reconciliation. Static RunInput bindings, the third-party two-method protocol and borrowed-service ownership remain compatible.

使用和恢复边界见[按需服务与状态](runtime.md#按需服务与状态--on-demand-services-and-status)。继续 44 个生产 Python 文件；既有文件净增 92 非空行，门禁按实际必需增长收至 17,480。零新增默认依赖、后台执行体、模型调用或持久化，不加入服务注册中心、状态聚合 API 或新调度器。只读状态不授予权限，也不提供任意同进程代码崩溃隔离。

See [on-demand services and status](runtime.md#按需服务与状态--on-demand-services-and-status) for usage and recovery boundaries. The release keeps 44 production Python files and adds 92 non-empty lines in existing files, with a measured source ceiling of 17,480. It adds no default dependencies, background workers, model calls or persistence, and no service registry, status aggregation API or scheduler. Read-only status grants no permissions and does not isolate crashes in arbitrary in-process code.

v0.2.125 增加被动资源的局部诊断与停用：损坏 Skill、MCP 描述或插件清单及其依赖从健康索引中排除，明确选中时返回 ResourceUnavailableError，其他健康方法仍可运行。不可变诊断和内容摘要参与快照身份，修复后显式 refresh 恢复；损坏副本/覆盖层不回退旧方法。Python 分页 API、模型 list_resource_diagnostics 和 CLI check 共用唯一 AgentLibrary，发行门禁拒绝内置资源受阻。保持零默认依赖、唯一 Runtime，无新增后台服务或持久化。

v0.2.125 adds local diagnosis and quarantine for passive resources: malformed Skills, MCP definitions, plugin manifests, and their dependents leave healthy indexes; explicit selection raises ResourceUnavailableError while unrelated methods remain runnable. Immutable diagnostics and content digests contribute to snapshot identity, with recovery after repair and explicit refresh; damaged copies or overlays never fall back to old methods. The Python paging API, model list_resource_diagnostics tool, and CLI check share the single AgentLibrary, and the release gate rejects unavailable builtins. There are no new default dependencies, Runtime, background services, or persistence.

兼容变化见[资源诊断](skills.md#损坏资源--damaged-resources)：可定位格式错误改为局部停用，普通缺失、循环、有效来源身份冲突、权限/扫描及记录失败仍直接报错。索引有故障时附诊断，CLI check 返回非零；移除资源要求完整健康引用索引。方法格式诊断不证明研究/算法结论正确，也不提供任意 Python 执行代码的强隔离。

See [resource diagnostics](skills.md#损坏资源--damaged-resources) for compatibility changes: identifiable format errors become local quarantine, while ordinary missing references, cycles, conflicting valid identities, permission/scan errors, and recording failures still fail directly. Faulty indexes include diagnostics and CLI check returns nonzero; removal requires a complete healthy reference index. Format diagnosis proves neither research/algorithm correctness nor strong isolation for arbitrary Python execution.

v0.2.124 收紧执行故障边界：显式工具超时及时返回并以 ToolOutcomeUnknown 结束运行；TaskQueue 保留 stop_unknown，取消、恢复和退回不会触发未知操作重放。stdio MCP 使用统一请求截止时间、非阻塞有界管道、stderr 排空和进程组回收；失联 tools/call 需宿主核对效果并按操作 ID 显式解除。默认可信工具仍同步调用，无新增默认依赖、持久化或后台服务。

v0.2.124 tightens execution fault boundaries: explicit tool timeouts return promptly and end the run with ToolOutcomeUnknown. TaskQueue preserves stop_unknown across cancellation, restore, and return-for-changes, preventing replay of unknown operations. Stdio MCP uses one request deadline, bounded nonblocking pipes, stderr draining, and process-group cleanup; lost tools/call requests require host reconciliation and explicit resolution by operation ID. Trusted tools remain synchronous by default, with no new default dependencies, persistence, or background service.

兼容变化与限制见[执行故障契约](runtime.md#执行故障边界--execution-fault-boundaries)：普通 Python 线程不提供强制停止或原生崩溃隔离，进程停止不撤销已发生的外部效果；被动资源局部诊断现已在 v0.2.125 实现。

See the [execution contract](runtime.md#执行故障边界--execution-fault-boundaries) for compatibility changes and limits: Python threads do not provide forced stop or native-crash isolation, process termination does not undo external effects, and local diagnosis of damaged passive resources is now available in v0.2.125.

v0.2.123 增加通用研究探索图：连接目标、方向、工作、任务与正/负结果，支持多来源谱系、前沿、预算、Mermaid 导出及显式检查点恢复。研究、算法、离线量化与深度优化复用同一探索 Skill；实验比较和主张/证据版本关联复用宿主 TaskCheck，证据变更或过期使旧验收失效。保持唯一 Runtime、零默认依赖，不提供攻击、渗透或实盘交易功能。

v0.2.123 adds a general research exploration graph linking goals, directions, work, tasks and positive/negative results, with multiple-source lineage, frontier queries, budgets, Mermaid export and explicit checkpoint recovery. Research, algorithms, offline quantitative studies and deep optimization share one exploration Skill. Experiment comparison and versioned claim/evidence links use host TaskCheck acceptance; changed or expired evidence invalidates prior acceptance. The release retains one Runtime and zero default dependencies, with no attack, penetration-testing or live-trading features.

用法见[探索图指南](exploration.md)和[通用研究指南](general-research.md)。离线示例实际验证 882 个算法 oracle 用例并完成验收与恢复；真实模型探索质量、长任务收益和投资表现仍未验证。

See the [exploration guide](exploration.md) and [general research guide](general-research.md). The offline example runs 882 algorithm oracle cases and demonstrates acceptance and recovery; research quality with real models, long-task benefits and investment performance remain unverified.

v0.2.92 完善标准 Skill 解析：支持 YAML 流列表与转义，内置 `code` Skill 增加宿主中立能力需求并将概述与工具绑定分离。

v0.2.92 improves standard Skill parsing with YAML flow lists and escapes, and adds host-neutral capability requirements to the builtin `code` Skill while separating the method narrative from host tool bindings.

v0.2.91 定义通用扩展包协议：固定 Agent Skills 规范版本、引入必需/可选能力 schema，兼容内联 YAML 映射，保留宿主扩展并输出结构化协议报告，附标准 Skill 资源 fixtures。

v0.2.91 defines the general extension package protocol: pins an Agent Skills spec version, adds required/optional capability schemas, accepts inline YAML mappings, preserves host extensions with a structured protocol report, and ships standard Skill resource fixtures.

v0.2.90 建立可解释的依赖方向门禁：核心层不得反向导入装配层，并分层报告 core/skill/adapter 源码规模；修正 `core.user` 对 skill 的顶层反向依赖。

v0.2.90 establishes an interpretable dependency-direction gate: the core layer must not reverse-import the assembly layer, and reports core/skill/adapter source sizes separately; it also fixes `core.user`'s top-level reverse dependency on skill.

v0.2.89 统一资源所有权与生命周期：创建者负责关闭，借用对象不由 Agent 关闭；`close()` 幂等并向子 Agent 传播，并存为上下文管理器。

v0.2.89 unifies resource ownership and lifecycle: the creator closes, and borrowed objects are not closed by the Agent; `close()` is idempotent, propagates to subagents, and is available as a context manager.

v0.2.88 收敛 Agent 公共入口：不再复制死配置，模型画像归领域对象，构造、run/stream、工具与子 Agent 组合是唯一面向初学者的入口。

v0.2.88 converges the Agent public entry: it no longer duplicates dead configuration, model profiles belong to domain objects, and construction, run/stream, tools, and subagent composition are the single beginner-facing entry.

v0.2.87 让基础 Agent 运行只载入必要模块：未启用的团队、存储与 MCP 实现不再被普通运行导入；启用这些能力时仍正常加载与工作。

v0.2.87 makes a base Agent run load only necessary modules: unenabled team, storage, and MCP implementations are no longer imported by a plain run, while they still load and work when enabled.

v0.2.86 统一到 `super_agent.*` 命名空间；在仓库外安装 wheel 可 `import super_agent`，并与宿主自己的 `core`/`skill` 包共存。

v0.2.86 unifies the codebase under the `super_agent.*` namespace; a wheel installed outside the repo supports `import super_agent` and coexists with a host project's own `core`/`skill` packages.

v0.2.85 让 CLI 直接安装、打包、列出、读取与移除本地 Skill，复用 AgentLibrary 的冲突检查与回滚，并修复同内容重装的幂等哈希口径。

v0.2.85 lets the CLI install, pack, list, read, and remove local Skills directly, reusing AgentLibrary conflict checks and rollback, and fixes the idempotent content-hash basis for same-content reinstalls.

v0.2.84 按项目标记探测候选验证命令；候选仍走命令白名单与逐次确认，不因探测自动放行，并返回 argv、cwd 与退出状态。

v0.2.84 probes candidate verification commands by project markers; candidates still pass the command whitelist and per-action confirmation, never auto-allowed by detection, and return argv, cwd, and exit status.

v0.2.83 统一模型配置诊断：`check` 逐模型报告密钥状态与下一步，不泄露密钥，也不静默切换 Provider。

v0.2.83 unifies model-configuration diagnostics: `check` reports per-model credential status and the next step, without leaking secrets or silently switching Provider.

v0.2.82 让 `--save` 自动创建会话并显示 ID，保存与恢复共用存储作为唯一历史来源；恢复时校验会话存在与作用域。

v0.2.82 makes `--save` auto-create a conversation and show its ID, with save and restore sharing storage as the single history source; restores validate existence and scope.

v0.2.81 让默认 CLI 连续聊天在内存中携带前序用户消息与助手回答，第二轮能看到第一轮。

v0.2.81 lets the default CLI chat carry prior user messages and assistant answers in memory, so the second turn sees the first.

v0.2.80 完成长会话治理：单调检查点、记忆索引检查点和严格作用域的显式日志清理。

v0.2.80 completes long-session governance with monotonic checkpoints, memory-index checkpoints, and explicitly scoped log cleanup.

检查通过后创建一个本地逻辑提交和标签；远程推送是单独的授权边界。

After checks pass, create one local logical commit and tag; pushing to a remote is a separate authorization boundary.
