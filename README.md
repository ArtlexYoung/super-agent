# Super Agent

[中文使用文档](README_cn.md)

[English guide](README_en.md)

**一个简单、轻量、高智能、自进化、Skill 优先的 Agent 运行时。**

**A simple, lightweight, highly intelligent, self-evolving, skill-first agent runtime.**

> Skill 即一切。

> Skill is all you need.

Super Agent 只向模型提供精简的 Skill 索引，由模型判断需要什么，再按需读取并执行对应内容。

Super Agent gives the model a compact Skill index, lets the model decide what it needs, and discloses and executes only that content.

提示、工具使用方法、记忆方法、工作流和任务策略都使用同一种 Skill 格式，并经过同一条中心化渐进式披露路径；模型连接和密钥仍由显式配置管理。

Prompts, tool-use methods, memory methods, workflows, and task policies share one Skill format and one central progressive-disclosure path; model connections and secrets remain explicit configuration.

默认 Python 安装没有第三方运行依赖，基础 `Agent()` 无状态、不写文件，存储、记忆、MCP 和学习都按需启用。

The default Python install has no third-party runtime dependencies, a basic `Agent()` is stateless and writes no files, and storage, memory, MCP, and learning are opt-in.

## 一分钟开始

*Start in One Minute*

需要 Python 3.11 或更高版本。

Python 3.11 or newer is required.

```bash
python3.11 -m pip install -e .
export OA3_SILICONFLOW_API_KEY="..."
super-agent check
super-agent "解释这个仓库"
```

不带参数运行 `super-agent` 会进入交互对话，直接传入文本则执行一次任务。

Run `super-agent` without arguments for an interactive conversation, or pass text directly for a one-shot task.

离线检查必须显式启用 Mock Provider，不会偷偷替换真实模型。

Offline checks must explicitly select the Mock Provider and never silently replace a real model.

```bash
SUPER_AGENT_PROVIDER=mock super-agent check
SUPER_AGENT_PROVIDER=mock super-agent "你好"
```

## 核心方式

*Core Model*

- **全 Skill：** 所有可组合内容都由 Skill 承载，Runtime 只调度被选中的机制。
  **Skill-first:** Skills carry all composable content, while Runtime only schedules selected mechanisms.
- **全自动：** 模型根据 Skill 描述、任务证据和模型配置作出选择，不依赖硬编码触发词。
  **Automatic:** The model chooses from Skill descriptions, task evidence, and model profiles without hard-coded trigger words.
- **全进化：** 评价、保鲜度、候选变更、测试、应用和撤销形成显式可审计闭环。
  **Self-evolving:** Evaluation, freshness, candidate changes, tests, application, and undo form an explicit auditable loop.
- **渐进式：** 大内容以稳定引用和有界分页披露，Skill、工具、记忆和子 Agent 共用一份运行预算。
  **Progressive:** Large content is disclosed through stable references and bounded pages, with Skills, tools, memory, and subagents sharing one run budget.
- **无隐藏退化：** 缺少模型、存储、权限、依赖或验证条件时直接失败，不伪造成功，也不静默降低能力。
  **No hidden fallback:** Missing models, storage, authority, dependencies, or verification fail visibly instead of inventing success or silently reducing behavior.
- **按需组合：** 基础运行不绑定存储、记忆、对话、安全规则或学习，多用户和多 Agent 状态仅在启用时存在。
  **Composable by need:** Basic runs do not require storage, memory, conversations, safety rules, or learning, and multiuser or multi-Agent state exists only when enabled.

## Python 用法

*Python Usage*

常用库入口只需要 `Agent` 和显式的环境模型辅助函数。

The common library entry point needs only `Agent` and the explicit environment-model helper.

```python
from super_agent import Agent, model_from_environment

agent = Agent(model_from_environment())
result = agent.run("解释 Skill 渐进式披露")
print(result.text)
```

组和子 Agent 在代码中自然组合。第 1 层是根组，普通组不调用模型，每个 Agent 仍保留自己的模型、Skill 和配置。

Groups and subagents compose naturally in code. Level 1 is the root group, structural groups do not call models, and each Agent keeps its own models, Skills, and configuration.

```python
from super_agent import Agent, model_from_environment

main = Agent(model_from_environment())
coder = Agent(model_from_environment())
engineering = main.add_group("engineering")
engineering.add_subagent(coder, name="coder", description="实现并验证代码修改")
result = main.run("让工程组修复失败的测试", skill="common-multi-producer-consumer")
```

同级组通过父组共享板交换带缓存路径的明确记录。任务队列、等待唤醒、价格和权重路由、断路重试、动态压缩及多模型决策都由同一个 `AgentTreeRuntime` 管理；不添加组或子 Agent 时不会创建这些状态。

Sibling groups exchange explicit records with cache paths through their parent board. One `AgentTreeRuntime` owns queues, sleep and wake events, price and weight routing, circuit retries, adaptive compression, and multi-model decisions; none of this state is created when no group or subagent is added.

## CLI

*CLI*

CLI 保持直接入口，配置检查是只读操作，单次任务默认无状态。

The CLI remains a direct entry point, configuration checks are read-only, and one-shot tasks are stateless by default.

```bash
super-agent check
super-agent --skill code "检查这个仓库"
super-agent config show
super-agent skills list
super-agent data storage verify --config common.toml
super-agent data conversations list --config common.toml --user alice
```

## 核心保证

*Core Guarantees*

- 读取不会修改业务状态；显式启用磁盘披露缓存时，只会写入有界、可丢弃的缓存文件。
  Reads do not mutate domain state; an explicitly configured disclosure cache writes only bounded, disposable cache files.
- Skill 内容是被动数据，不能自行注册代码、权限或密钥。
  Skill content is passive data and cannot register code, permissions, or secrets by itself.
- Provider、工具、存储和可选功能错误会保留原始失败语义。
  Provider, tool, storage, and optional-feature errors retain their original failure semantics.
- 用户与 Agent 范围隔离对话、记忆、运行记录、披露缓存和 Skill 覆盖层。
  User and Agent scopes isolate conversations, memory, runs, disclosure caches, and Skill overlays.
- 运行输入、选择、工具调用、评价和 Skill 变更都可追踪，模型正文无需重复写入审计摘要。
  Run inputs, selections, tool calls, evaluations, and Skill changes are traceable without duplicating model text in audit summaries.

## 评测成绩

*Benchmark Results*

以下 Agent 使用同一个 `THUDM/GLM-4-9B-0414` 模型和各自默认配置，成绩为通过题数 / 总题数（通过率）。

The agents below use the same `THUDM/GLM-4-9B-0414` model with their default configurations, and scores are passed tasks / total tasks (pass rate).

| Agent | 模型 / Model | HumanEval+ | LiveCodeBench Codegen |
| --- | --- | ---: | ---: |
| Codex | `THUDM/GLM-4-9B-0414` | 96 / 164 (58.54%) | 166 / 612 (27.12%) |
| Claude Code | `THUDM/GLM-4-9B-0414` | 100 / 164 (60.98%) | 151 / 612 (24.67%) |
| Super Agent | `THUDM/GLM-4-9B-0414` | 103 / 164 (62.80%) | 156 / 612 (25.49%) |

完整任务级报告、隔离运行器和本地评测资产说明位于 [`tests/eval/`](tests/eval/README.md)。

Full task-level reports, isolated runners, and local evaluation asset guidance live under [`tests/eval/`](tests/eval/README.md).

## 使用文档

*Usage Guides*

完整使用说明按语言拆分，双语首页只保留最短上手路径与项目边界。

The complete usage guide is split by language, while this bilingual overview keeps only the shortest onboarding path and project boundaries.

- [中文使用文档](README_cn.md)
- [English guide](README_en.md)
- [源码阅读路径 / Source tour](docs/source-tour.md)
- [Skill](docs/skills.md)
- [配置 / Configuration](docs/configuration.md)
- [Runtime](docs/runtime.md)
- [CLI](docs/cli.md)
- [学习、记忆与 Skill 更新 / Learning, memory, and Skill changes](docs/evolution.md)
- [安全 / Safety](docs/safety.md)
- [评测 / Benchmarks](docs/benchmarks.md)

## 致谢与借鉴

*Acknowledgements and Design References*

Super Agent 感谢以下项目的作者与贡献者，项目名称和模块路径以 `v0.2.0` 编写时公开内容为准。

Super Agent thanks the authors and contributors below, with project names and module paths referring to the public sources available when `v0.2.0` was written.

除明确列出的协议约定外，Agent 架构均在 Python 中独立实现，致谢不表示复制或捆绑对应运行时代码。

Except for the explicitly listed protocol contracts, the Agent architecture is independently implemented in Python, and acknowledgement does not imply vendoring the referenced runtime code.

### 开源 Agent 架构

*Open-source Agent Architecture*

- **[OpenAI Codex](https://github.com/openai/codex)（Apache-2.0）：** 研究了 `codex-rs/core` 的 turn 生命周期、上下文与工具路由，`exec` 的受控进程和事件输出，`execpolicy` 的动作判定，`apply-patch` 的结构化修改，以及 `skills` 的发现与解析；这些经验用于代码任务链、显式副作用检查、有界命令、受检查文件修改和渐进式 Skill 披露。
  **[OpenAI Codex](https://github.com/openai/codex) (Apache-2.0):** We studied turn lifecycle, context, and tool routing in `codex-rs/core`, controlled processes and event output in `exec`, action decisions in `execpolicy`, structured edits in `apply-patch`, and discovery and parsing in `skills`; these informed the coding task chain, explicit side-effect checks, bounded commands, checked file changes, and progressive Skill disclosure.
- **[Hermes Agent](https://github.com/NousResearch/hermes-agent)（MIT）：** 研究了 `agent/conversation_loop.py`、`tool_executor.py`、`tool_guardrails.py`、`memory_manager.py`、`skill_*` 和 `subagent_lifecycle.py`；这些模块启发了通用任务链、工具边界、记忆整理、Skill 组织和子 Agent 委派流程。
  **[Hermes Agent](https://github.com/NousResearch/hermes-agent) (MIT):** We studied `agent/conversation_loop.py`, `tool_executor.py`, `tool_guardrails.py`, `memory_manager.py`, `skill_*`, and `subagent_lifecycle.py`; these modules informed the general task chain, tool boundaries, memory organization, Skill organization, and subagent delegation flow.
- **[OpenClaw](https://github.com/openclaw/openclaw)（MIT）：** 研究了 `src/agents`、`src/memory`、`src/sessions`、配置层和 `skills` 目录；这些结构启发了代码式多 Agent 组合、用户与 Agent 状态隔离、可复用 Skill 目录和通用任务工作流。
  **[OpenClaw](https://github.com/openclaw/openclaw) (MIT):** We studied `src/agents`, `src/memory`, `src/sessions`, configuration layers, and the `skills` tree; these structures informed code-first multi-Agent composition, user and Agent state isolation, reusable Skill catalogs, and general task workflows.
- **[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（MIT）：** 研究了 `packages/core/agent-loop`、`packages/core/session`、`packages/core/tools`、`packages/core/scope`、bundle/profile 组合和事件流水线；其“一切皆插件”思路启发了可选机制由 Skill 挂载、运行事件作为事实来源、运行范围资源所有权和可替换边界。
  **[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (MIT):** We studied `packages/core/agent-loop`, `packages/core/session`, `packages/core/tools`, `packages/core/scope`, bundle/profile composition, and the event pipeline; its “everything is a plugin” model informed Skill-attached optional mechanisms, runtime events as facts, run-scoped resource ownership, and replaceable boundaries.

### 论文与组合模型

*Research and Composition Model*

- **[《A Programming Paradigm for Spatiotemporal Composability》](https://github.com/cordiverse/paper)：** 论文中的可逆副作用、响应式共作用和统一上下文启发了原子 Skill 激活、逆序资源清理、显式 Runtime 需求、稳定运行快照和组合故障门禁；Super Agent 没有声称实现 Cordis 演算，而是采用了适合轻量 Python Runtime 的工程约束。
  **[A Programming Paradigm for Spatiotemporal Composability](https://github.com/cordiverse/paper):** Its revertible effects, reactive coeffects, and unified context informed atomic Skill activation, reverse-order resource cleanup, explicit Runtime needs, stable run snapshots, and composability fault gates; Super Agent does not claim to implement the Cordis calculus and instead adopts engineering constraints suitable for a lightweight Python Runtime.

### 协议

*Protocols*

- **[Model Context Protocol](https://github.com/modelcontextprotocol/modelcontextprotocol)：** `initialize`、`tools/list`、`tools/call` 和 stdio JSON-RPC 约定用于 MCP 适配器，Super Agent 额外要求实现与副作用在可信代码中显式注册。
  **[Model Context Protocol](https://github.com/modelcontextprotocol/modelcontextprotocol):** The MCP adapter follows `initialize`, `tools/list`, `tools/call`, and stdio JSON-RPC contracts, while Super Agent additionally requires implementations and side effects to be registered explicitly in trusted code.

### 评测项目

*Evaluation Projects*

- **[EvalPlus](https://github.com/evalplus/evalplus)：** HumanEval+ 数据与执行规则用于代码正确性对照评测，不进入 Super Agent Runtime。
  **[EvalPlus](https://github.com/evalplus/evalplus):** HumanEval+ data and execution rules are used for comparative code-correctness evaluation and are not part of Super Agent Runtime.
- **[LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench)：** Code Generation 数据与评测约定用于公开结果表，不进入 Super Agent Runtime。
  **[LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench):** Code Generation data and evaluation contracts are used for the published result table and are not part of Super Agent Runtime.

### 公开但非开源的参考

*Public but Non-open-source Reference*

- **[Claude Code](https://github.com/anthropics/claude-code)：** 其仓库受 Anthropic 商业条款约束，本项目只研究公开插件、SDK 和文档，主要参考 `feature-dev` 的探索/设计/实现阶段、`code-review` 与 `pr-review-toolkit` 的多视角复核、`plugin-dev` 的 Skill 结构和 `security-guidance` 的操作前提醒，没有将 Claude Code Runtime 代码视为开源代码。
  **[Claude Code](https://github.com/anthropics/claude-code):** Its repository is governed by Anthropic commercial terms, so this project studies only public plugins, SDKs, and documentation, mainly the exploration/design/implementation phases in `feature-dev`, multi-perspective review in `code-review` and `pr-review-toolkit`, Skill structure in `plugin-dev`, and pre-action reminders in `security-guidance`, without treating Claude Code Runtime code as open source.

第三方项目保留各自版权与许可证，本项目许可证见 [LICENSE](LICENSE)。

Third-party projects retain their own copyrights and licenses, and this project is licensed under [LICENSE](LICENSE).

## 验证仓库

*Verify the Repository*

完整发布门禁会检查 Python 测试、编译、包内容、离线评测和构建。

The full release gate checks Python tests, compilation, package contents, offline evaluation, and build.

```bash
python3.11 scripts/verify_release.py --version 0.2.1 --full
```
