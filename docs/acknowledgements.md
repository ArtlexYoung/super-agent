# 致谢与借鉴 / Acknowledgements and Design References

本文说明 Super Agent 研究过哪些公开项目、论文和协议，哪些设计受到启发，以及哪些内容没有被复制或绑定。

This document records the public projects, paper, and protocol studied by Super Agent, the resulting design influence, and the material that was not copied or coupled.

## 范围 / Scope

以下内容以 Super Agent `v0.2.1` 的公开仓库状态为准。这里的“借鉴”表示阅读公开代码、文档、插件、协议或研究成果后形成工程上的设计判断，不表示逐行复制、代码生成来源确认或功能等价实现。

The notes below describe the public repository state used for Super Agent `v0.2.1`. “Studied” means that public code, documentation, plugins, protocols, or research informed engineering decisions; it does not mean line-by-line copying, provenance certification for generated code, or feature equivalence.

## 参考总览 / Reference Overview

| 参考 / Reference | 研究范围 / Studied area | 对 Super Agent 的影响 / Influence |
| --- | --- | --- |
| [OpenAI Codex](https://github.com/openai/codex) | `codex-rs/core`、`exec`、`execpolicy`、`apply-patch`、`skills` | turn 循环、工具路由、受控副作用、结构化文件修改、渐进式 Skill 披露 |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | `conversation_loop.py`、`tool_executor.py`、`tool_guardrails.py`、`memory_manager.py`、`skill_*`、`subagent_lifecycle.py` | 通用任务循环、工具边界、记忆整理、Skill 组织、子 Agent 委派 |
| [OpenClaw](https://github.com/openclaw/openclaw) | `src/agents`、`src/memory`、`src/sessions`、配置层、`skills` | 代码式 Agent 组合、用户和 Agent 状态隔离、会话与 Skill 目录 |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | `agent-loop`、`session`、`tools`、`scope`、bundle/profile、事件流水线 | 可选机制挂载、运行事件事实源、运行范围资源所有权、可替换边界 |
| [Claude Code](https://github.com/anthropics/claude-code) | 公开插件、SDK 和文档；`feature-dev`、`code-review`、`pr-review-toolkit`、`plugin-dev`、`security-guidance` | 代码任务分阶段执行、多视角复核、Skill 结构、操作前提醒 |
| [Agent Skills](https://github.com/agentskills/agentskills) | `SKILL.md` 规范、YAML front matter、标准资源目录、渐进披露 | Skill 的可移植目录格式、标准字段和资源边界 |
| [《A Programming Paradigm for Spatiotemporal Composability》](https://github.com/cordiverse/paper) | 可逆副作用、响应式共作用、统一上下文 | 原子 Skill 激活、逆序清理、显式 Runtime 需求、组合约束 |
| [Model Context Protocol](https://github.com/modelcontextprotocol/modelcontextprotocol) | `initialize`、`tools/list`、`tools/call`、stdio JSON-RPC | MCP 适配边界和显式工具注册 |
| [EvalPlus](https://github.com/evalplus/evalplus) / [LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench) | HumanEval+、Code Generation 数据与执行规则 | 代码能力对照评测，不进入 Runtime |

The English summary is the same: the table maps each public reference to the studied area and the Super Agent design boundary it influenced.

## 具体借鉴 / Detailed Influence

### OpenAI Codex

Codex 的 turn 生命周期、上下文组织和工具路由帮助我们明确 Runtime 的基本边界；`exec` 和事件输出启发了有界进程、真实状态回传和显式副作用；`execpolicy` 启发了动作权限分级；`apply-patch` 启发了带前置读取哈希的结构化修改；`skills` 启发了索引优先、命中后再读取正文的渐进式披露。

Codex informed the Runtime boundary through its turn lifecycle, context organization, and tool routing. `exec` and event output influenced bounded processes and real state reporting; `execpolicy` influenced action permission levels; `apply-patch` influenced hash-checked structured edits; and `skills` influenced index-first progressive disclosure.

### Hermes Agent

Hermes 的对话循环、工具执行器和工具护栏帮助我们拆分模型决策与工具执行；记忆管理器和 Skill 相关模块启发了短期上下文、长期记忆及 Skill 内容的分离；子 Agent 生命周期启发了委派、等待、唤醒和结果汇总的接口。

Hermes influenced the separation between model decisions and tool execution through its conversation loop, executor, and guardrails. Its memory and Skill modules informed the separation of short-term context, long-term memory, and Skill content. Its subagent lifecycle informed delegation, waiting, waking, and result aggregation interfaces.

### OpenClaw

OpenClaw 的 Agent、memory、sessions、配置和 skills 目录展示了多用户状态、会话状态与能力目录可以清晰分层。Super Agent 因此使用代码组合 Agent 树，并按用户隔离会话、记忆、运行记录和 Skill 覆盖层。

OpenClaw showed how Agents, memory, sessions, configuration, and a Skill tree can be separated while supporting multiple users. This informed code-defined Agent trees and user-scoped isolation for conversations, memory, runs, and Skill overlays.

### DeepSeek Harness

DeepSeek Harness 的 agent loop、session、tools、scope、bundle/profile 和事件流水线启发了“可选机制由 Skill 声明，Runtime 只负责调度”的边界。运行事件作为事实来源，使等待、重试、审计和结果压缩可以共享同一条记录路径。

DeepSeek Harness informed the boundary in which Skills declare optional mechanisms while the Runtime schedules them. Treating run events as facts allows waiting, retries, audit records, and result compression to share one event path.

### Claude Code

Claude Code 不是本项目的开源 Runtime 依赖。我们只参考公开插件、SDK 和文档中可观察的代码工作流：先探索，再设计和实现，最后验证；同时参考多视角代码复核、插件目录结构和敏感操作前的提醒。

Claude Code is not an open-source Runtime dependency of this project. We reference only observable workflows in public plugins, SDKs, and documentation: explore, design and implement, then verify; we also study multi-perspective review, plugin layout, and reminders before sensitive actions.

### Agent Skills

Super Agent 直接采用 Agent Skills 的 `<name>/SKILL.md`、YAML front matter、`references/`、`scripts/` 和 `assets/` 约定，并保持“索引、正文、资源”三级渐进披露。Super Agent 自己的类型、工具依赖、组合和更新权限只放在标准字符串 `metadata` 中，不新增私有顶层字段。

Super Agent directly adopts Agent Skills conventions for `<name>/SKILL.md`, YAML front matter, `references/`, `scripts/`, and `assets/`, preserving metadata/body/resource progressive disclosure. Super Agent type, tool dependency, composition, and update-authority extensions live only in the standard string-valued `metadata` map, without private top-level fields.

### 时空可组合性论文

论文中的可逆副作用、响应式共作用和统一上下文为能力组合提供了抽象参考。Super Agent 只将这些思想转化为轻量工程约束，例如 Skill 激活失败时不留下半成品状态、资源按逆序清理、运行上下文明确声明需求；本项目不声称实现论文中的 Cordis 演算。

The paper provides an abstraction for composable effects, reactive coeffects, and unified context. Super Agent translates these ideas only into lightweight engineering constraints, such as atomic Skill activation, reverse-order cleanup, and explicit Runtime requirements; it does not claim to implement the Cordis calculus.

### Model Context Protocol

MCP 的初始化、能力发现、工具调用和 stdio JSON-RPC 约定用于 MCP 适配器的协议边界。协议本身不替代 Super Agent 的 Skill、Runtime 或安全判断；工具实现和副作用仍必须由可信代码显式注册。

MCP initialization, capability discovery, tool calls, and stdio JSON-RPC define the protocol boundary for the MCP adapter. The protocol does not replace Super Agent Skills, Runtime, or safety decisions; tool implementations and side effects still require explicit registration in trusted code.

### 评测项目

EvalPlus 的 HumanEval+ 和 LiveCodeBench 的 Code Generation 数据与执行规则只用于代码能力对照。评测数据、任务运行器和报告位于 `tests/eval/`，不会被 `src/` 的运行时自动加载，也不是默认安装依赖。

EvalPlus HumanEval+ and LiveCodeBench Code Generation data and execution rules are used only for comparative code evaluation. Datasets, runners, and reports live under `tests/eval/`; the `src/` Runtime does not load them automatically, and they are not default install dependencies.

## 没有复制或绑定的内容 / What We Do Not Copy or Couple

- Super Agent 的 Runtime、Provider、Skill 解析和 Agent 组织树使用独立的 Python 实现。
- Super Agent Runtime, Provider, Skill parsing, and Agent organization tree are independently implemented in Python.
- 参考项目不会成为运行时的必需依赖；默认安装只使用 Python 标准库。
- Referenced projects are not required Runtime dependencies; the default install uses only the Python standard library.
- 公开协议只定义适配边界，不会把第三方 SDK、工具实现或权限模型偷偷带入核心。
- Public protocols define adapter boundaries; third-party SDKs, tool implementations, and permission models are not silently brought into the core.
- Skill 是被动内容，不能仅凭 Markdown 注册 Python 代码、密钥或权限；执行机制必须由用户或可信代码显式提供。
- Skills are passive content. Markdown alone cannot register Python code, secrets, or permissions; execution mechanisms must be supplied explicitly by the user or trusted code.
- 评测中的第三方快照和数据保留在 `tests/eval/`，不代表它们属于 Super Agent 源码或许可证。
- Third-party snapshots and datasets in `tests/eval/` remain evaluation assets and are not represented as Super Agent source code or as part of its license.

## 许可证与版权 / Licensing and Copyright

Super Agent 自身使用仓库根目录 [Apache License 2.0](../LICENSE)。Codex、Hermes Agent、OpenClaw、DeepSeek Harness 和 Agent Skills 的许可证信息以各自上游仓库和随附许可证文件为准；本文只记录研究时的公开声明，不替代上游许可证文本。

Super Agent is licensed under the [Apache License 2.0](../LICENSE). License information for Codex, Hermes Agent, OpenClaw, DeepSeek Harness, and Agent Skills is governed by each upstream repository and its included license files; this document records the public notices observed during study and does not replace upstream license text.

Claude Code 的仓库、插件、SDK 和文档受其各自的商业或其他适用条款约束。本项目不把 Claude Code Runtime 代码作为开源代码使用，也不把其作为安装依赖。

The Claude Code repository, plugins, SDK, and documentation are governed by their respective commercial or other applicable terms. This project does not treat Claude Code Runtime code as open-source code or make it an installation dependency.

评测数据、论文和协议可能有独立的版权、引用和再分发要求。分发或修改相关资产前，应阅读对应上游仓库中的 `LICENSE`、`NOTICE`、数据集条款和引用说明。

Benchmark data, papers, and protocols may have separate copyright, citation, and redistribution requirements. Read the corresponding upstream `LICENSE`, `NOTICE`, dataset terms, and citation guidance before distributing or modifying those assets.

如发现归属、链接或许可证信息需要修正，请提交具体来源和文件路径；项目会在不改变运行时边界的前提下更新这份说明。

If an attribution, link, or license note needs correction, please provide the concrete source and file path; this document can be updated without changing the Runtime boundary.
