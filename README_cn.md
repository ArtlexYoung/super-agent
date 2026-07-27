# Super Agent

[English](README.md)

> Skill is all you need.

Super Agent 是一个**简单、轻量、自进化、Skill 优先的 Agent 运行时**。

它用于验证一个核心想法：prompt、工具、记忆、workflow、模型和其他 Agent 行为，都可以声明为 Skill，按需渐进披露，由 Capability 执行，根据真实运行进行评价，并持续进化。

项目目前处于实验性的 `0.0.x` 阶段。相比过早承诺 API 兼容，当前更重视运行时足够小、容易审计，以及每次破坏性调整都清晰明确。

## 为什么使用 Super Agent

- **零配置启动**：`Agent()` 和 CLI 可以直接运行，默认使用本地 mock 模型。
- **统一 Skill 格式**：prompt、MCP、memory、workflow、模型和可执行机制共用 manifest 与生命周期。
- **渐进式披露**：模型先看到轻量索引，只打开任务真正需要的 Skill。
- **自动任务调度**：Runtime 自动选择兼容的模型、Skill 和子 Agent，并从用户隔离的质量、可靠性、延迟和成本证据中持续学习。
- **统一运行生命周期**：发现、披露、执行、观察、评价和进化共用一个会话。
- **自动进化信号**：Runtime 根据真实失败、质量、保鲜度、替代行为、成本和延迟生成去重后的进化建议。
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

零配置模式会自动从环境中发现模型凭据：

```bash
export OPENAI_API_KEY="..."
super-agent models resolve
super-agent run "总结这个项目"
```

系统也会发现 `ANTHROPIC_API_KEY` 和 `OLLAMA_HOST`。只有找不到任何可用模型时，Runtime 才会创建一个临时 mock profile。

当模型需要持久化名称、说明、默认状态或路由特征时，创建一个 model Skill：

```text
skills/model/fast/skill.toml
```

```toml
schema_version = 2
name = "fast"
capability = "model"
description = "适合摘要和简单问题的低延迟模型"
version = "0.1.0"
triggers = ["快速", "摘要"]
agent_can_update = true

[configuration]
provider = "openai-compatible"
model = "gpt-4.1-mini"
api_key_env = "OPENAI_API_KEY"
supports = ["text", "tools", "json"]
purposes = ["summary"]
default = true
```

model Skill 与其他 Skill 共用中心索引、校验、证据、候选、晋升和回滚流程。详见 [配置说明](docs/configuration.md)。

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

Skill 的稳定身份统一使用 `capability:name`，例如 `prompt:concise`、`memory:default`、`workflow:direct` 和 `model:fast`。纯配置 Skill 不需要 `SKILL.md`。

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

`Agent.run(...)` 会创建唯一的内部 `TaskRequest`，并交给 `AgentRuntime.run_task(...)`。`TaskScheduler` 自动选择有序模型回退列表、渐进匹配的 Skill 和命中的子 Agent。完整原因记录在 `task.scheduled`；每次模型尝试都会记录选择、完成或失败、延迟、估算 token 和成本。Workflow Skill 只保存指令与结束规则，模型和工具循环统一由 Runtime 管理。

模型路由在冷启动时保持确定性，只有同一任务用途积累证据后才进行有界探索。证据按用户、Agent、model Skill 和任务用途隔离。需要时可以直接记录质量分数：

```python
result = agent.run("总结报告", user_id="alice")
agent.record_task_feedback(result.run_id, 0.25, "遗漏了结论", user_id="alice")
```

```bash
super-agent runs feedback --run-id <run-id> --score 0.25 --reason "遗漏了结论"
```

在持久化会话中，明确纠正或重复同一请求也会确定性地降低上一轮质量。显式反馈始终覆盖隐式信号，两种反馈分类都不调用大模型。

`CapabilityRegistry` 只保存真正执行 Skill 的处理器，可以通过 `agent.add_skill_executor(...)` 明确替换。需要安装或进化的处理器使用标准 `capability` Skill，与其他 Skill 共用披露、评价、晋升和回滚；Runtime 生命周期不再允许替换成另一套并行控制器。

## 可复现证明

[v0.0.34 实验说明](docs/experiments/v0.0.34.md)及其[生成的 JSON 报告](docs/experiments/v0.0.34.json)比较了无 Skill、全量加载与渐进披露三种上下文，并验证完整生命周期和存储隔离。报告发布后，实验编排代码已从发行 Runtime 删除，避免证明代码长期膨胀用户内核。

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

候选以完整 Skill 目录为单位，因此 prompt、memory、workflow、MCP、model、可执行 Capability 和自定义 Skill 共用同一套生命周期。模型只返回明确的完整文件写入与删除操作；Runtime 会在评价前校验路径、身份、权限和类型专属配置。候选不会直接覆盖正在使用的 Skill，只有评价通过且父版本没有变化时才能晋升，每个已晋升版本都可以回滚。

model Skill 的连接字段默认归用户所有。Agent 可以改进说明、触发词、优势、用途和路由特征，但只有用户设置 `agent_can_update_connection = true` 后，Agent 才能修改 `provider`、`model`、`base_url` 或 `api_key_env`。

可执行机制使用 `capability` Skill 和普通 Skill 命令：

```bash
super-agent skills evolve \
  --config agent.toml \
  --name capability:careful \
  --goal "减少运行失败" \
  --cases evaluation-cases.json
```

保鲜度计算不调用大模型，而是根据质量、距离上次调用时间、使用频率、token 成本、延迟、可靠性、同功能替代行为和样本置信度确定性派生。

每次运行完成评价后，Runtime 会自动检查所有允许更新的 Skill，包括由 Skill 承载的可执行机制。这个确定性步骤不调用大模型，也不增加任何配置项。只有当前证据达到质量或效率阈值时才会记录进化建议，同一份未变化的证据不会反复生成建议。

```bash
super-agent evolution list --config agent.toml
super-agent evolution show --config agent.toml --schedule-id <id> --output json
super-agent evolution create-candidate --config agent.toml --schedule-id <id>
```

创建候选时才会调用已配置的模型，并记录候选实际新增、修改和删除的文件。候选仍然必须通过原有的隔离评价才能晋升；自动调度本身不会修改或激活正在使用的目标。

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

JSONL 会在 `.super-agent/users/<user-hash>/events.jsonl` 下为每个用户保存一条可读的标准事件流。会话、运行、评价、记忆、使用习惯、Skill 保鲜度、进化建议和披露历史都按用户与 Agent 隔离。`RuntimeStore` 从事件派生领域视图；渐进披露缓存和进化工作区只是按用户隔离、可重新生成的本地产物。

需要更强的本地并发时，可以选择 Python 标准库自带的 SQLite，Runtime 语义完全相同，也不会增加第三方依赖：

```toml
[storage]
backend = "sqlite"
path = ".super-agent"
```

数据库保存在 `.super-agent/events.sqlite3`，并启用 WAL。JSONL 仍是默认后端，因为它可以直接阅读，也不需要数据库文件。

多进程或服务部署只需安装实际使用的数据库驱动。连接地址只放在环境变量中，不会写入 TOML：

```bash
python3 -m pip install 'super-agent[postgresql]'
export SUPER_AGENT_POSTGRESQL_URL='postgresql://user:password@host/super_agent'
```

```toml
[storage]
backend = "postgresql"
path = ".super-agent"
```

MySQL 的用法相同，对应 `super-agent[mysql]` 和 `SUPER_AGENT_MYSQL_URL`。只有需要更换环境变量名时才填写 `url_env`。使用远程后端时，标准事件保存在数据库中，`path` 仍用于本地渐进披露缓存和进化工作区。

所有会读写状态的 Python 与 CLI 操作都接受用户身份；不填写时仍使用零配置的 `local` 用户：

```bash
super-agent run --user-id alice --conversation-id project-a "继续任务"
super-agent conversations list --user-id alice
```

可以按用户在任意已配置后端之间复制事件，不改变领域数据：

```bash
super-agent storage copy \
  --config agent.toml \
  --to-backend sqlite \
  --to-path .super-agent-sqlite \
  --user-id alice
```

远程目标使用 `--to-backend mysql` 或 `postgresql`，需要自定义连接环境变量名时再添加 `--to-url-env CUSTOM_DATABASE_URL`。

运行锁也作为运行事件保存，用于固定选中的模型 profile、Provider 适配器、存储后端、Capability 版本、Skill 版本和 Skill 目录哈希，但不会保存密钥内容。

## 详细文档

- [快速开始](docs/getting-started.md)
- [架构](docs/architecture.md)
- [Capability](docs/capabilities.md)
- [Skill 与渐进式披露](docs/skills.md)
- [Runtime、Workflow、追踪与多 Agent](docs/runtime.md)
- [评价、保鲜度、记忆与进化](docs/evolution.md)
- [可复现的 v0.0.34 实验](docs/experiments/v0.0.34.md)
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
