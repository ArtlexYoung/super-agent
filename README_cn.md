# Super Agent

[English](README.md)

> Skill is all you need.

Super Agent 是一个**简单、轻量、自进化、Skill 优先的 Agent 运行时**。

它用于验证一个核心想法：prompt、工具、记忆、workflow 和其他 Agent 行为，都可以声明为 Skill，按需渐进披露，由 Capability 执行，根据真实运行进行评价，并持续进化。

项目目前处于实验性的 `0.0.x` 阶段。相比过早承诺 API 兼容，当前更重视运行时足够小、容易审计，以及每次破坏性调整都清晰明确。

## 为什么使用 Super Agent

- **零配置启动**：`Agent()` 和 CLI 可以直接运行，默认使用本地 mock 模型。
- **统一 Skill 格式**：prompt、MCP、memory 和 workflow 共用 manifest 与发现路径。
- **渐进式披露**：模型先看到轻量索引，只打开任务真正需要的 Skill。
- **统一运行生命周期**：发现、披露、执行、观察、评价和进化共用一个会话。
- **代码式多 Agent**：每个 Agent 独立创建，再通过 `Agent.add_subagent(...)` 组合。
- **标准库运行时**：Python 核心没有第三方运行依赖。

## 30 秒开始使用

需要 Python 3.11 或更高版本。在项目根目录执行：

```bash
python3 -m pip install -e .
super-agent run "你好"
```

首次运行不需要配置文件或 API key。如果没有发现可用模型，Super Agent 会自动使用确定性的本地 mock provider。

进入交互对话：

```bash
super-agent
```

作为 Python 库使用：

```python
from super_agent import Agent

result = Agent().run("解释什么是渐进式 Skill 披露")
print(result.text)
```

只有需要持久化多轮上下文时，才需要显式创建会话：

```python
agent = Agent()
conversation = agent.create_conversation()
agent.run("记住我的项目使用 Python", conversation_id=conversation.conversation_id)
result = agent.run("项目使用什么语言？", conversation_id=conversation.conversation_id)
```

## 使用真实模型

模型配置会被自动发现。例如：

```bash
export OPENAI_API_KEY="..."
super-agent models resolve
super-agent run "总结这个项目"
```

系统也会发现 `ANTHROPIC_API_KEY` 和 `OLLAMA_HOST`。显式 TOML 配置是可选的，并且始终拥有最高优先级。详见 [配置说明](docs/configuration.md)。

## 初始化项目

只有需要编辑 Agent 配置或 Skill 时才需要初始化：

```bash
super-agent init --path my-agent
super-agent run --config my-agent/agent.toml "你好"
```

生成的项目包含一个 Agent 配置，以及 prompt、MCP、memory 和 workflow 示例 Skill。

## 创建 Skill

一个 Skill 是包含 `skill.toml` 和可选内容文件的目录：

```text
skills/prompt/concise/
  skill.toml
  SKILL.md
```

```toml
schema_version = 2
name = "concise"
capability = "prompt"
description = "使用尽可能少的文字清晰回答"
version = "0.1.0"
triggers = ["简短", "精简"]

[entry]
instructions = "SKILL.md"
```

```markdown
使用短句，只保留回答当前问题所需的信息。
```

运行能够命中该 Skill 的任务：

```bash
super-agent run --config agent.toml "请简短解释这个概念"
```

Skill 的稳定身份统一使用 `capability:name`，例如 `prompt:concise`、`memory:default` 和 `workflow:direct`。纯配置 Skill 不需要 `SKILL.md`。

## 工作原理

Super Agent 明确划分五类职责：

```text
Provider   提供模型智能
Runtime    管理统一生命周期
Capability 执行具体机制
Skill      承载内容与配置
Agent      组合所有部分
```

每次运行都遵循同一条中心生命周期：

```text
discover -> disclose -> execute -> observe -> evaluate -> evolve
```

`RuntimeSession` 是一次运行唯一的共享上下文，集中保存一个 `RunIdentity`、一个中心化 `RuntimeStore`、唯一 Skill 索引、渐进披露会话，以及真正影响结果的 Skill 和 Capability。Capability 只消费该会话，不再分别创建存储或重复扫描 Skill 目录。

## 自进化

Agent 自己创建的 Skill 可以声明允许更新：

```toml
agent_created = true
agent_can_update = true
```

更新遵循有证据的闭环：

```text
create candidate -> validate -> evaluate -> promote -> rollback
```

```bash
super-agent skills evolve \
  --config agent.toml \
  --name concise \
  --goal "让回答更清晰" \
  --cases evaluation-cases.json
```

候选不会直接覆盖正在使用的 Skill。只有评价通过且父版本没有变化时才能晋升，每个已晋升版本都可以回滚。当前已完整支持指令型 Skill 的闭环；所有 Skill 类型的统一进化计划在 `v0.0.30` 完成。

保鲜度计算不调用大模型，而是根据质量、距离上次调用时间、使用频率、token 成本、延迟、可靠性、同功能替代行为和样本置信度确定性派生。

## 多 Agent 组合

Agent 关系使用容易阅读的 Python 代码，而不是写死在 TOML 中：

```python
from super_agent import Agent

main = Agent.load_from_config_file("agents/main.toml")
coder = Agent.load_from_config_file("agents/coder.toml")
reviewer = Agent.load_from_config_file("agents/reviewer.toml")

main.add_subagent(coder, name="coder", triggers=["代码", "实现"])
main.add_subagent(reviewer, triggers=["审查"])

result = main.run("实现并审查这个功能")
```

不填写 `name` 时会依次生成 `subagent01`、`subagent02`。多层或循环 Agent 链路只会产生清晰警告，不会被运行时强制终止；最终结束条件由 workflow 决定。

## 运行状态

所有可变运行状态共用一个存储后端和一套语义 API。默认配置不需要额外依赖，也不需要手动填写：

```toml
[storage]
backend = "jsonl"
path = ".super-agent"
```

JSONL 会在 `.super-agent/users/<user-hash>/events.jsonl` 下为每个用户保存一条可读的标准事件流。会话、运行、评价、记忆、使用习惯、Skill 保鲜度、进化证据和披露历史都按用户与 Agent 隔离。`RuntimeStore` 从事件派生领域视图；渐进披露缓存和进化工作区只是按用户隔离、可重新生成的本地产物。

需要更强的本地并发时，可以选择 Python 标准库自带的 SQLite，Runtime 语义完全相同，也不会增加第三方依赖：

```toml
[storage]
backend = "sqlite"
path = ".super-agent"
```

数据库保存在 `.super-agent/events.sqlite3`，并启用 WAL。JSONL 仍是默认后端，因为它可以直接阅读，也不需要数据库文件。

所有会读写状态的 Python 与 CLI 操作都接受用户身份；不填写时仍使用零配置的 `local` 用户：

```bash
super-agent run --user-id alice --conversation-id project-a "继续任务"
super-agent conversations list --user-id alice
```

可以按用户把事件复制到另一个本地后端，不改变领域数据：

```bash
super-agent storage copy \
  --config agent.toml \
  --to-backend sqlite \
  --to-path .super-agent-sqlite \
  --user-id alice
```

运行锁也作为运行事件保存，用于固定实际使用的 Provider、存储后端、Capability 版本、Skill 版本和 Skill 目录哈希，但不会保存密钥内容。

## 详细文档

- [快速开始](docs/getting-started.md)
- [架构](docs/architecture.md)
- [Skill 与渐进式披露](docs/skills.md)
- [Runtime、Workflow、追踪与多 Agent](docs/runtime.md)
- [评价、保鲜度、记忆与进化](docs/evolution.md)
- [CLI 命令](docs/cli.md)
- [配置说明](docs/configuration.md)
- [macOS 应用](docs/macos.md)
- [后续路线](docs/roadmap.md)

## 开发验证

运行 Python 测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

检查 Python 导入和 Swift 前端：

```bash
PYTHONPATH=src python3 -m compileall -q src
swift build --package-path src/frontend/mac
```

公开 Python API 统一从 `super_agent` 导入。`0.0.x` 阶段内部模块不会保留兼容薄壳。
