# Super Agent

[English README](README.md)

> Skill is all you need.

Super Agent 是一个**简单、轻量、自进化、Skill 优先的 Agent 运行时**。

提示、工具、记忆、工作流、模型说明和规划规则都是 Skill。统一的渐进式披露核心只在
需要时发现和加载内容；统一的 Runtime 负责调度、记录证据，并基于真实结果改进允许更新的
Agent 自建 Skill，不会产生第二套执行链路。

项目仍处于实验性的 `0.0.x` 阶段。为了验证 Skill-first 模型，破坏性修改会被明确执行，
不会保留隐式兼容层。

## 快速开始

需要 Python 3.11 或更高版本。Python Runtime 不依赖任何第三方库。

```bash
python3 -m pip install -e .
export OPENAI_API_KEY="..."
super-agent "解释这个仓库"
```

也会自动发现 `ANTHROPIC_API_KEY` 和 `OLLAMA_HOST`。不需要先创建 `agent.toml`，也不
需要初始化项目。完全离线演示时，请显式选择 Mock Provider：

```bash
SUPER_AGENT_PROVIDER=mock super-agent "你好"
```

Super Agent 不会偷偷创建 Mock，也不会在 Provider 失败后偷偷切换模型。没有模型或调用
失败时会直接返回清楚的错误。

启动连续对话或可选 Web 客户端：

```bash
super-agent
super-agent serve
```

Web 地址是 `http://127.0.0.1:8765/`，包含原生 AG-UI 对话、可视化配置、运行树、
记忆管理、Skill 保鲜度，以及直接连接 `POST /ag-ui` 的 CopilotKit 示例。

## Python 库

```python
from super_agent import Agent

agent = Agent()
result = agent.run("解释渐进式 Skill 披露")
print(result.text)
print(result.run_id)
```

应用代码可以显式注入自己的 Provider、存储、动作规则、SkillRunner 和子 Agent。CLI 只是
同一个 Python 内核的入口，不是另一套 Runtime。

## 一个心智模型

```text
Provider     连接模型智能
Core         调度任务并管理状态
SkillRunner  将一种 Skill 类型变成行为
Skill        承载被动内容和配置
Agent        用清晰的 Python 代码组合一切
```

每个任务只经过一条路径：

```text
Agent.run
  -> Core 创建唯一运行会话
  -> 渐进式披露选择 Skill
  -> SkillRunner 加载已选 Skill
  -> Core 选择模型并执行任务
  -> 记录事件、评价、保鲜度和进化证据
```

没有独立的记忆引擎、工作流引擎、MCP 引擎或规划引擎。它们都是由 SkillRunner 加载的
普通 Skill 类型。渐进式披露核心也可以独立进行只读发现，只有调用者明确要求时才写入缓存
和历史。

## 创建 Skill

```text
skills/prompt/concise/
  skill.toml
  SKILL.md
```

```toml
schema_version = 3
name = "concise"
type = "prompt"
description = "用最短的内容给出有用回答"
version = "0.1.0"
triggers = ["简短", "精简"]
agent_created = false
agent_can_update = false

[entry]
instructions = "SKILL.md"
```

稳定标识是 `type:name`，例如 `prompt:concise`、`memory:default` 和 `model:fast`。
自定义类型只需在代码中调用匹配的 `Agent.add_skill_runner(...)`；Core 没有写死 Skill
类型列表。

需要一个可编辑示例项目时再运行 `super-agent init --path my-agent`。初始化是可选工具，
不是使用前提。

## 只配置真正需要的内容

`Agent()` 依次检查 `SUPER_AGENT_CONFIG`、当前目录的 `agent.toml`，最后使用内存默认值。
完整的最小配置如下：

```toml
[agent]
name = "demo"
system = "You are a concise, helpful agent."
skills = []
disabled_skills = []

[paths]
skills = ["skills"]

[storage]
backend = "jsonl"
path = ".super-agent"
```

`skills` 表示每次任务都固定加载的 Skill；未固定的 Skill 仍可被自动选择。
`disabled_skills` 可以排除一种类型、一个稳定标识或无歧义名称。模型配置是普通的 model
Skill，不是特殊的 Agent 字段；密钥只保存在环境变量中。

## 自动调度与自进化

- model Skill 声明用途、特性、质量、延迟、token 成本和连接环境变量。Core 在每次模型
  调用前，根据这些声明和当前用户的真实证据选择已就绪模型。
- Runtime 事件记录模型选择、Skill 披露、工具、子 Agent、token 估算、动作决定、结果和
  失败，但不保存密钥值。
- Skill 保鲜度不调用大模型，而是综合使用次数、结果、时间间隔、频率、token 成本以及同
  功能 Skill 的成功替代情况计算。
- `agent_can_update = true` 的 Agent 自建 Skill 可以创建候选、执行不退化评价、晋升完整
  Skill 目录、持续监控并在退化时回滚。共享项目 Skill 始终是只读基线。
- 临时记忆严格绑定当前会话，无法进入其他会话；长期记忆只用于抽象、关键、重要、稳定或
  习惯性的内容。
- 整理长期记忆时，模型可以查看当前会话的相关临时记忆，并显式提升其中的抽象内容。临时
  来源仍保留在原会话中，来源条目 ID 会进入可审计事件。
- 回忆只能通过校验后的操作合并、替换、归档、遗忘或提升记忆。每次变化都是显式事件；模型
  返回无效整理结果时会失败，不会偷偷返回未整理内容。

直接查看证据：

```bash
super-agent runs explain --run-id <run-id>
super-agent skills index --output json
super-agent skills freshness
super-agent evolution list
```

## 多用户与多 Agent

通过 `Agent.for_user(...)` 一次绑定用户。会话、记忆、Skill 使用情况、披露历史、模型证据、
Provider 缓存、用户 Skill 覆盖层和进化状态都会按用户和 Agent 隔离。

```python
main = Agent("agents/main.toml")
coder = Agent("agents/coder.toml")
reviewer = Agent("agents/reviewer.toml")

main.add_subagent(coder, name="coder", triggers=["代码", "实现"])
main.add_subagent(reviewer, triggers=["审查"])
result = main.for_user("alice").run("实现并审查这个改动")
```

省略名称时会依次生成 `subagent01`、`subagent02`。深层或循环链路会在执行前给出完整路径
警告，但不会被静默阻断。可选的 `max_agent_chain_depth` 只控制警告阈值，最终停止条件由
workflow Skill 定义。

## 存储与显式副作用

默认存储是 `.super-agent/` 下可直接阅读、零依赖的 JSONL。SQLite 同样只使用标准库；
MySQL 和 PostgreSQL 只在选择后安装对应可选依赖。所有后端实现同一个用户隔离事件契约。

每个有副作用的工具都要声明资源和 `read`、`create`、`update`、`delete`、`execute`、
`network` 或 `delegate` 效果。Core 在 handler 运行前检查这些效果。Skill 文本只是未受信任
上下文，不能给自己授予执行权限，也不存在未声明动作的退化路径。

## 文档

- [快速开始](docs/getting-started.md)
- [架构](docs/architecture.md)
- [Skill 与渐进式披露](docs/skills.md)
- [SkillRunner](docs/skill-runners.md)
- [配置](docs/configuration.md)
- [Core、追踪与多 Agent](docs/runtime.md)
- [评价、记忆与进化](docs/evolution.md)
- [动作规则](docs/safety.md)
- [CLI 参考](docs/cli.md)
- [Web 客户端](docs/web.md)
- [AG-UI](docs/ag-ui.md)
- [路线图](docs/roadmap.md)

## 开发

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
PYTHONPATH=src:tests python3 -m compileall -q src tests docs/experiments
pnpm --dir web lint
pnpm --dir web typecheck
pnpm --dir web build
```

公共 Python API 统一从 `super_agent` 导出。`0.0.x` 阶段不提供内部导入兼容层。
