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
- **自动规划**：复杂任务由渐进披露的 Planner Skill 拆解，简单任务仍只进行一次直接模型调用。
- **自动任务调度**：Runtime 自动选择兼容的模型、Skill 和子 Agent，并从用户隔离的质量、可靠性、延迟和成本证据中持续学习。
- **统一运行生命周期**：发现、披露、执行、观察、评价和进化共用一个会话。
- **统一安全边界**：Capability 执行前，Runtime 会记录每个动作声明的 effect。
- **默认安全**：内部记忆和自有 Skill 进化保持自动化；外部执行和未知网络动作需要批准。
- **自动进化闭环**：Runtime 根据真实证据创建候选，自动评价和晋升，并持续监控和回滚退化版本。
- **能记也能忘的记忆**：回忆时同步整理重复、过时和低价值记忆，所有变化都使用可验证、可追溯的事件。
- **内置 Web 客户端**：一条命令即可使用中文对话、配置、记忆、Skill、模型和运行视图，所有数据仍来自同一个 Runtime。
- **原生 AG-UI 接入**：零 Python 依赖的 HTTP/SSE 桥接层直接发送 Runtime 标准事件，不建立第二条执行路径。
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
conversation = agent.for_user("local").conversations.create()
agent.run("记住我的项目使用 Python", conversation_id=conversation.conversation_id)
result = agent.run("项目使用什么语言？", conversation_id=conversation.conversation_id)
```

打开内置 Web 客户端：

```bash
super-agent serve
```

浏览器访问 `http://127.0.0.1:8765/`。同一服务的 `/ag-ui` 是实时事件端点；Web 客户端直接使用 Runtime 持久化的会话和配置，不在浏览器维护另一份状态。详见 [Web 客户端](docs/web.md)和 [AG-UI 桥接](docs/ag-ui.md)。

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

Web 客户端提供中文可视化 model Skill 编辑器。模型元数据写入当前用户的 Skill 覆盖层，凭据只配置环境变量名，真实 API Key 不会写入浏览器状态、TOML 或运行记录。

## 初始化项目

只有需要编辑 Agent 配置或 Skill 时才需要初始化：

```bash
super-agent init --path my-agent
super-agent run --config my-agent/agent.toml "你好"
```

生成的项目包含一个 Agent 配置，以及 prompt、MCP、memory、workflow 和 Planner 示例 Skill。

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

`Agent.run(...)` 会创建唯一的内部 `TaskRequest`，并交给 `AgentRuntime.run_task(...)`。一个中心自适应任务循环选择有序模型回退列表、渐进匹配的 Skill 和命中的子 Agent，然后连续推进模型与工具步骤。完整原因记录在 `task.scheduled`；每次模型尝试都会记录选择、完成或失败、延迟、估算 token 和成本。Workflow Skill 只保存指令与结束规则。

内置 `planner:default` Skill 让自动规划保持零配置。每个任务都有一个 `TaskPlan`；简单任务使用确定性的一步计划，不增加规划模型调用。复杂提示、结构化多步骤请求、额外能力要求和 `plan` workflow 会让所选模型返回严格计划。每个步骤都通过同一个模型、Skill、工具和子 Agent 循环执行。在项目中创建同名 `planner:default`，即可通过同一个中心 Skill 索引覆盖内置后备版本。

模型路由在冷启动时保持确定性，只有同一任务用途积累证据后才进行有界探索。证据按用户、Agent、model Skill 和任务用途隔离。需要时可以直接记录质量分数：

每次路由都会在运行轨迹中给出置信度和证据充分度。Runtime 会自动用证据充分的模型替代低置信度候选；若没有可靠替代，则明确记录不确定性并保留普通失败回退链。用户不需要配置路由阈值。

```python
alice = agent.for_user("alice")
result = alice.run("总结报告")
alice.runs.record_feedback(result.run_id, 0.25, "遗漏了结论")
```

```bash
super-agent runs feedback --run-id <run-id> --score 0.25 --reason "遗漏了结论"
```

在持久化会话中，明确纠正或重复同一请求也会确定性地降低上一轮质量。显式反馈始终覆盖隐式信号，两种反馈分类都不调用大模型。

可以通过 CLI 或 Web 客户端的任务树查看同一份中心证据：

```bash
super-agent runs explain --config agent.toml --run-id <run-id>
super-agent runs explain --config agent.toml --run-id <run-id> --output json
```

洞察投影包含调度原因、任务计划及完成步骤、每一步的模型与子 Agent 路由、模型尝试、延迟、估算 token 与成本、学习后的路由证据、相关 Skill 保鲜度和自动进化决策。子 Agent 的 `run_id` 也可以从主项目查看，但查询仍严格限制在同一用户和同一存储后端内。

`CapabilityRegistry` 只保存由应用代码明确注册的 Skill 执行机制，可以通过 `agent.add_capability(...)` 替换。每个 Capability 只有一个 `load_skill(request)` 契约，并统一返回 `SkillContribution`，因此 Runtime 不需要了解 Memory、MCP 或 Workflow 的具体类。Skill 目录始终是不可信的声明式内容，Runtime 不会从中导入或执行 Python。

每个 Capability 工具都必须显式声明 `read`、`create`、`update`、`delete`、`execute`、`network` 或 `delegate` 效果，不存在隐式默认权限。声明不完整的 Capability 会在 handler 运行前失败；带 Runtime 身份的 Skill 加载、记忆整理和习惯更新也必须经过同一个 Safety 执行器。Skill 内容本身不能提供或降低权限。

## 可复现证明

[v0.0.53 端到端 Runtime 证明](docs/experiments/v0.0.53.md)及其[生成的 JSON 报告](docs/experiments/v0.0.53.json)会启动真实 AG-UI HTTP 服务，并执行唯一的标准任务路径。这次运行会在回忆时整理并遗忘记忆，在 handler 执行前拦截外部 Tool，通过 `RUN_ERROR` 发送失败，并把发生失败的 Agent 自建 Skill 从 `0.1.0` 自动晋升到 `0.1.1`。整个证明确定性、本地运行、零依赖，也不需要 API Key。

[v0.0.46 证明](docs/experiments/v0.0.46.md)覆盖计划任务中的模型与子 Agent 路由，以及 Planner/model Skill 进化。此前的 [v0.0.41 证明](docs/experiments/v0.0.41.md)验证用户与 Agent 证据隔离和自动回滚，[v0.0.34 生命周期实验](docs/experiments/v0.0.34.md)比较无 Skill、全量加载和渐进披露上下文。实验编排始终位于发行 Runtime 之外，避免证明代码变成第二套执行框架。

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

候选以完整 Skill 目录为单位，因此 prompt、memory、workflow、MCP、model 和自定义声明式 Skill 共用同一套生命周期。模型只返回明确的完整文件写入与删除操作；Runtime 会在评价前校验路径、身份、动作策略和类型专属配置。候选不会直接覆盖正在使用的 Skill，只有评价通过且父版本没有变化时才能晋升，每个已晋升版本都可以回滚。

model Skill 的连接字段默认归用户所有。Agent 可以改进说明、触发词、优势、用途和路由特征，但只有用户设置 `agent_can_update_connection = true` 后，Agent 才能修改 `provider`、`model`、`base_url` 或 `api_key_env`。

可执行 Capability 是受信任的应用代码，必须通过 `Agent.add_capability(...)` 明确注册。Runtime 会拒绝 `capability = "capability"` 的 Skill；Agent 可以进化注册代码使用的声明式 Skill，但不能把下载或生成的 Python 自动提升到 Runtime 进程。

保鲜度计算不调用大模型，而是根据质量、距离上次调用时间、使用频率、token 成本、延迟、可靠性、同功能替代行为和样本置信度确定性派生。

记忆会在回忆过程中同步整理。Runtime 先排序候选记忆并确定性合并完全重复项，再让当前模型只返回严格的 `merge`、`supersede`、`archive` 或 `forget` 操作。每个操作都必须引用本次召回的候选项，并经过同一套 Safety 策略执行。归档和遗忘只会让记忆退出活动视图，不会从只追加事件历史中物理删除。只有需要纯只读检索时，才在 memory Skill 中设置 `organize_on_recall = false`。

每次运行完成评价后，Runtime 会自动检查所有允许更新且由 Agent 创建的 Skill。确定性进化调度器对同一份未变化的证据最多生成一次建议；自动进化服务随后通过同一个自适应模型调用路径生成完整目录候选，使用触发运行中的最多三个真实 prompt 进行评价，只有通过现有证据门槛后才会晋升。

```bash
super-agent evolution list --config agent.toml
super-agent evolution show --config agent.toml --evolution-id <id> --output json
```

`evolution` 命令只用于查看自动状态。晋升后的版本会继续接受真实运行监控：任何失败都会触发回滚；三个健康样本会将其标记为稳定；三个样本后的平均分低于 `0.75` 同样会触发回滚。自动化异常只记录在任务追踪中，不会替换主任务结果。

## 多 Agent 组合

Agent 关系使用容易阅读的 Python 代码，而不是写死在 TOML 中：

```python
from super_agent import Agent

main = Agent("agents/main.toml")
coder = Agent("agents/coder.toml")
reviewer = Agent("agents/reviewer.toml")

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

JSONL 会在 `.super-agent/users/<user-hash>/events.jsonl` 下为每个用户保存一条可读的标准事件流。会话、运行、评价、记忆、使用习惯、Skill 保鲜度、进化建议和披露历史都按用户与 Agent 隔离。`RuntimeStore` 负责公共作用域和运行操作；`store.disclosure` 负责披露缓存与历史，`store.memory` 负责同一后端上的记忆与习惯操作。渐进披露缓存和进化工作区只是按用户隔离、可重新生成的本地产物。

可变 Skill 使用同一作用域。Runtime 通过唯一中心索引按 `用户 > 项目 > 内置` 的顺序解析。安装、模型编辑和候选晋升都写入 `.super-agent/users/<user-hash>/agents/<agent-hash>/skills`，不会改动共享的项目 Skill。删除用户 Skill 后会重新露出下层共享 Skill；用户级管理器不能删除共享 Skill。

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

每次运行会一起解析该用户的 Skill 索引、模型列表、路由证据、Provider 缓存和密钥。多用户服务可以通过代码提供密钥，无需把值写入 TOML 或运行状态：

```python
secrets = {
    ("alice", "OPENAI_API_KEY"): "...",
    ("bob", "OPENAI_API_KEY"): "...",
}
agent = Agent(secret_lookup=lambda user_id, name: secrets.get((user_id, name)))
```

回调只会收到校验后的用户 ID 和请求的环境变量名，其视图不可枚举，带用户凭据的 Provider 实例不会跨用户复用，密钥值也不会进入事件或运行锁。由应用代码显式注册的 Provider 仍归应用管理。不传 `secret_lookup` 时仍直接使用进程环境，保持原有零配置体验。

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
- [可复现的 v0.0.53 端到端 Runtime 证明](docs/experiments/v0.0.53.md)
- [可复现的 v0.0.46 规划与进化证明](docs/experiments/v0.0.46.md)
- [可复现的 v0.0.41 任务与进化证明](docs/experiments/v0.0.41.md)
- [可复现的 v0.0.34 实验](docs/experiments/v0.0.34.md)
- [CLI 命令](docs/cli.md)
- [配置说明](docs/configuration.md)
- [Web 客户端](docs/web.md)
- [AG-UI 桥接](docs/ag-ui.md)
- [后续路线](docs/roadmap.md)

## 开发验证

运行 Python 测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

检查 Python 导入并构建可选 Web 客户端：

```bash
PYTHONPATH=src python3 -m compileall -q src
cd web
pnpm lint
pnpm build
```

公开 Python API 统一从 `super_agent` 导入。`0.0.x` 阶段内部模块不会保留兼容薄壳。
