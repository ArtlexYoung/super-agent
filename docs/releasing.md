# 发布 / Releasing

## 本地检查 / Local Checks

使用 Python 3.11 和 `uv`：

Use Python 3.11 and `uv`:

```bash
PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 \
python3.11 -m unittest discover -s tests -p 'test_*.py' -v

PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 \
python3.11 scripts/verify_release.py --version 0.2.89 --full
```

发布检查验证版本、Python 最低版本、零默认依赖、源码布局、旧目录删除、构建范围、内置插件去重关系和离线评测资产。

The release gate checks version, Python minimum, zero default dependencies, source layout, removed legacy directories, build scope, builtin plugin deduplication, and offline evaluation assets.

日志写入不会触发保留清理。先运行 `super-agent data storage prune --config common.toml --user alice` 预览，确认后追加 `--apply` 才删除当前用户、Agent 和工作目录中的到期日志并记录清理事件；状态流不会因为期限被删除。

Log writes never trigger retention cleanup. Run `super-agent data storage prune --config common.toml --user alice` to preview, then add `--apply` to delete expired logs in the current user, Agent, and working-directory scope and record the cleanup; state streams are not expired by this policy.

## 版本 / Version

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
