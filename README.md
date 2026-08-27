# Super Agent

[中文使用文档](README_cn.md)

[English guide](README_en.md)

**一个简单、轻量、高智能、自进化、Skill 优先的 Agent 运行时。**

**A simple, lightweight, highly intelligent, self-evolving, skill-first agent runtime.**

> Skill 即一切。

> Skill is all you need.

Super Agent 只向模型提供精简的插件与 Skill 索引，由模型判断需要什么，再按需读取并激活对应内容。

Super Agent gives the model compact plugin and Skill indexes, lets the model decide what it needs, and discloses and activates only that content.

提示、工具使用方法、记忆方法、工作流和任务策略都使用同一种 Skill 格式，并经过同一条中心化渐进式披露路径；模型连接和密钥仍由显式配置管理。

Prompts, tool-use methods, memory methods, workflows, and task policies share one Skill format and one central progressive-disclosure path; model connections and secrets remain explicit configuration.

中央资源库统一管理 Skill 与 MCP；插件只保存规范引用，因此多个插件共享内容时不会重复安装。

The central library manages Skills and MCP definitions; plugins keep canonical references only, so shared content is never installed twice.

每个 Skill 继续直接采用 Agent Skills 标准的 `SKILL.md`、YAML front matter 和 Markdown 正文；MCP 描述不包含命令、地址、密钥或自动连接逻辑。

Every Skill still uses standard Agent Skills `SKILL.md`, YAML front matter, and Markdown; MCP definitions contain no command, endpoint, secret, or automatic connection logic.

默认 Python 安装没有第三方运行依赖，基础 `Agent()` 无状态、不写文件，存储、记忆、MCP 和学习都按需启用。

The default Python install has no third-party runtime dependencies, a basic `Agent()` is stateless and writes no files, and storage, memory, MCP, and learning are opt-in.

## 一分钟开始

*Start in One Minute*

需要 Python 3.11 或更高版本。

Python 3.11 or newer is required.

```bash
python3.11 -m pip install -e .
SUPER_AGENT_PROVIDER=mock super-agent check
SUPER_AGENT_PROVIDER=mock super-agent "解释这个仓库"
```

不带参数运行 `super-agent` 会进入交互对话，直接传入文本则执行一次任务。

Run `super-agent` without arguments for an interactive conversation, or pass text directly for a one-shot task.

远程模型必须通过通用环境变量或 `common.toml` 显式配置；项目不识别私人变量名，也不会猜测供应商、模型或地址。

Remote models must be configured explicitly through generic environment variables or `common.toml`; the project does not recognize private variable names or guess a provider, model, or endpoint.

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

Groups and subagents compose naturally in code. Level 1 is the root group, structural groups do not call models, and each Agent keeps its own models, plugins, Skills, and configuration.

```python
from super_agent import Agent, model_from_environment

main = Agent(model_from_environment())
coder = Agent(model_from_environment())
engineering = main.add_group("engineering")
engineering.add_subagent(coder, name="coder", description="实现并验证代码修改")
result = main.run(
    "让工程组修复失败的测试",
    skill="skill:super-agent/common/multi-agent",
)
```

同级组通过父组共享板交换带缓存路径的明确记录。任务队列、等待唤醒、价格和权重路由、断路重试、动态压缩及多模型决策都由同一个 `AgentTreeRuntime` 管理；不添加组或子 Agent 时不会创建这些状态。

Sibling groups exchange explicit records with cache paths through their parent board. One `AgentTreeRuntime` owns queues, sleep and wake events, price and weight routing, circuit retries, adaptive compression, and multi-model decisions; none of this state is created when no group or subagent is added.

模型可以带有用户填写的初始 `description`；系统保留这份先验，并把按用户、Agent 和任务类型学习出的可靠性与显式质量评价作为独立画像附加，而不是覆盖原文。

Models may carry a user-authored initial `description`; the system preserves that prior and appends separately learned reliability and explicit quality evidence by user, Agent, and task type instead of overwriting it.

`skill:super-agent/common/review` 让至少两个不同 Agent 独立检视同一材料，再交叉验证发现；多样性不足时明确失败，不退化成执行者自检。

`skill:super-agent/common/review` assigns the same artifact to at least two distinct Agents and then cross-checks findings; insufficient diversity fails explicitly instead of degrading to executor self-review.

## CLI

*CLI*

CLI 保持直接入口，配置检查是只读操作，单次任务默认无状态。

The CLI remains a direct entry point, configuration checks are read-only, and one-shot tasks are stateless by default.

```bash
super-agent check
super-agent --plugin plugin:super-agent/code "检查这个仓库"
super-agent config show
super-agent plugins list
super-agent skills list
super-agent mcps list
super-agent data storage verify --config common.toml
super-agent data storage prune --config common.toml --user alice
super-agent data storage prune --config common.toml --user alice --apply
super-agent data conversations list --config common.toml --user alice
```

## 核心保证

*Core Guarantees*

- 读取不会修改业务状态；显式启用磁盘披露缓存时，只会写入有界、可丢弃的缓存文件。
  Reads do not mutate domain state; an explicitly configured disclosure cache writes only bounded, disposable cache files.
- 插件、Skill 和 MCP 描述都是被动数据，不能自行注册代码、连接、权限、密钥或进化授权。
  Plugin, Skill, and MCP descriptions are passive data and cannot register code, connections, permissions, secrets, or evolution authority.
- 同类型、ID、版本和内容跨来源只保留一个逻辑对象；同身份不同内容直接冲突，运行中固定使用启动时快照。
  Matching type, ID, version, and content share one logical object; divergent identities fail, and each run uses its starting snapshot.
- Provider、工具、存储和可选功能错误会保留原始失败语义。
  Provider, tool, storage, and optional-feature errors retain their original failure semantics.
- 用户与 Agent 范围隔离对话、记忆、运行记录、披露缓存和 Skill 覆盖层。
  User and Agent scopes isolate conversations, memory, runs, disclosure caches, and Skill overlays.
- 运行输入、选择、工具调用、评价和 Skill 变更都可追踪，模型正文无需重复写入审计摘要。
  Run inputs, selections, tool calls, evaluations, and Skill changes are traceable without duplicating model text in audit summaries.
- 日志清理先预览，只有 `--apply` 才删除到期详细或关键记录；检查点恢复也必须由调用方显式提供。
  Retention cleanup previews first and deletes only with `--apply`; checkpoint recovery also requires an explicit caller-provided store.

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

Super Agent 感谢以下项目、论文和协议。下表只保留可扫描的借鉴范围；模块、设计影响、许可证和边界说明见[致谢与借鉴详细文档](docs/acknowledgements.md)。

Super Agent thanks the projects, paper, and protocol below. This table keeps only the scannable summary; see the [detailed acknowledgements](docs/acknowledgements.md) for modules, design impact, licensing, and boundaries.

| 项目 / Reference | 类型与许可证 / Type and license | 主要借鉴 / Main influence |
| --- | --- | --- |
| [OpenAI Codex](https://github.com/openai/codex) | Agent runtime，Apache-2.0 | turn、工具路由、受控执行、结构化补丁、Skill 发现 / turns, routing, controlled execution, patches, Skill discovery |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Agent framework，MIT | 对话循环、工具边界、记忆、Skill、子 Agent 生命周期 / loop, tool boundaries, memory, Skills, subagents |
| [OpenClaw](https://github.com/openclaw/openclaw) | Agent framework，MIT | Agent、会话、记忆、配置和 Skill 的组合与隔离 / composition and isolation |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Harness，MIT | loop、session、scope、工具和事件流水线 / loops, sessions, scopes, tools, events |
| [Claude Code](https://github.com/anthropics/claude-code) | 公开产品，商业条款 / public product, commercial terms | 公开插件、SDK、文档中的代码流程与多视角复核 / public plugins, SDK, docs, coding flow, review |
| [Agent Skills](https://agentskills.io/specification) | 开放规范，Apache-2.0 / open specification, Apache-2.0 | `SKILL.md`、YAML 元数据、目录资源和渐进披露 / `SKILL.md`, YAML metadata, directory resources, progressive disclosure |
| [时空可组合性论文](https://github.com/cordiverse/paper) | 论文 / paper | 可逆副作用、响应式共作用、统一上下文 / reversible effects, coeffects, unified context |
| [Model Context Protocol](https://github.com/modelcontextprotocol/modelcontextprotocol) | 协议 / protocol | initialize、工具发现、工具调用和 stdio JSON-RPC / initialization, discovery, calls, stdio JSON-RPC |
| [EvalPlus](https://github.com/evalplus/evalplus) / [LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench) | 评测项目 / evaluation projects | 代码任务数据、执行规则和结果对照 / coding tasks, execution rules, comparison |

第三方项目保留各自版权与许可证；本项目许可证见 [LICENSE](LICENSE)。

Third-party projects retain their own copyrights and licenses; this project is licensed under [LICENSE](LICENSE).

## 验证仓库

*Verify the Repository*

完整发布门禁会检查 Python 测试、编译、包内容、离线评测和构建。

The full release gate checks Python tests, compilation, package contents, offline evaluation, and build.

```bash
python3.11 scripts/verify_release.py --version 0.2.17 --full
```
