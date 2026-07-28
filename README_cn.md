# Super Agent

[English](README.md)

> Skill is all you need.

Super Agent 是一个**简单、轻量、自进化、Skill 优先的 Agent 运行时**。

Prompt、工具、记忆、工作流、模型说明和其他 Agent 行为，都使用同一种 Skill
格式和同一套生命周期。Runtime 只渐进披露当前需要的内容，通过已注册的
Capability 执行机制，记录真实结果，并根据这些证据改进 Agent 自己创建的 Skill。

项目目前处于实验性的 `0.0.x` 阶段。在验证核心模型期间，破坏性调整会明确说明，
不会用兼容薄壳掩盖变化。

## 立即使用

需要 Python 3.11 或更高版本。在仓库根目录执行：

```bash
python3 -m pip install -e .
super-agent "解释一下这个仓库"
```

这就是完整的新手路径。不需要配置、额外依赖、API Key，也不用先初始化项目。
没有发现真实模型时，Runtime 会使用内置的确定性 mock，所有功能仍然可以运行。

进入连续对话：

```bash
super-agent
```

打开 React Web 客户端：

```bash
super-agent serve
```

然后访问 `http://127.0.0.1:8765/`。同一个服务还在 `/ag-ui` 提供 AG-UI
事件流，在 `/api` 下提供基于 Runtime 状态的管理接口。

也可以直接作为 Python 库使用：

```python
from super_agent import Agent

result = Agent().run("解释渐进式 Skill 披露")
print(result.text)
```

## 连接真实模型

最常见的用法只需设置一个环境变量，命令不变：

```bash
export OPENAI_API_KEY="..."
super-agent "总结这个项目"
```

Runtime 也会自动发现 `ANTHROPIC_API_KEY` 和 `OLLAMA_HOST`。只有没有找到任何
已配置模型时，才会使用内置 mock。

需要固定名称、说明、路由特征或自定义地址时，再创建 model Skill：

```toml
# skills/model/fast/skill.toml
schema_version = 2
name = "fast"
capability = "model"
description = "适合摘要任务的低延迟模型"
version = "0.1.0"
triggers = ["摘要"]

[configuration]
provider = "openai-compatible"
model = "gpt-4.1-mini"
api_key_env = "OPENAI_API_KEY"
supports = ["text", "tools"]
purposes = ["summary"]
default = true
```

Web 客户端编辑的也是同一种 model Skill。Skill 文件只保存密钥对应的环境变量名，
不会保存密钥值。所有可选路由特征见[配置说明](docs/configuration.md)。

## 一套心智模型

Super Agent 只划分五种职责：

```text
Provider    提供模型智能
Runtime     负责调度和任务生命周期
Capability  执行可信机制
Skill       承载被动内容与配置
Agent       组合模型、Capability、存储和子 Agent
```

每个任务都经过同一条中心路径：

```text
发现 -> 披露 -> 执行 -> 观察 -> 评价 -> 进化
```

系统里只有一个渐进披露核心、一个自适应任务循环、一个动作安全边界和一个标准事件
存储。Workflow 与 Planner Skill 只是策略，不是另一套执行引擎；AG-UI 和 Web
客户端也只是同一份 Runtime 状态的视图。

## 创建 Skill

一个 Skill 就是包含 `skill.toml` 和可选内容文件的目录：

```text
skills/prompt/concise/
  skill.toml
  SKILL.md
```

```toml
schema_version = 2
name = "concise"
capability = "prompt"
description = "用尽量少的文字清楚回答"
version = "0.1.0"
triggers = ["简短", "精简"]

[entry]
instructions = "SKILL.md"
```

```markdown
使用短句，只保留回答当前问题所需的信息。
```

Prompt、MCP、memory、workflow、Planner、model 和自定义声明式 Skill 共用同一个
索引与生命周期。稳定标识统一使用 `capability:name`，例如 `prompt:concise`、
`memory:default` 或 `model:fast`。

只有需要可编辑示例时才初始化项目：

```bash
super-agent init --path my-agent
super-agent run --config my-agent/agent.toml "请简短回答"
```

## 自动行为

- **渐进披露**：Runtime 先加载紧凑索引，只在需要时继续读取 manifest、说明、配置和
  资源。披露路径和缓存命中都会保留在运行历史中。
- **规划与调度**：简单任务只有一个直接步骤，复杂任务可以使用 `planner:default`。
  每一步会根据声明特征，以及当前用户自己的质量、可靠性、延迟、token 和成本证据，
  选择合适的模型与子 Agent。
- **记忆维护**：回忆时可以合并、替换、归档或遗忘过期内容。每次变化都经过校验并保留
  为事件，活动记忆视图则保持干净。
- **自进化**：Agent 自建且允许更新的 Skill 可以生成候选、接受评价、晋升到当前用户的
  覆盖层，并在后续表现退化时回滚。
- **统一安全层**：每个可执行工具都必须声明 `read`、`update`、`execute`、`network`
  等效果。默认策略允许内部自动维护，但有风险的外部动作必须先获得批准，handler 才能运行。

使用同一套 CLI 查看真实过程：

```bash
super-agent runs explain --run-id <run-id>
super-agent skills index --output json
super-agent evolution list
```

## 多用户与多 Agent

使用 `Agent.for_user(...)` 一次绑定用户。会话、记忆、模型路由证据、Skill 覆盖层、
披露缓存、进化状态、模型列表、Provider 缓存和可选密钥都会留在该用户作用域内。

```python
secrets = {
    ("alice", "OPENAI_API_KEY"): "...",
    ("bob", "OPENAI_API_KEY"): "...",
}

agent = Agent(secret_lookup=lambda user_id, name: secrets.get((user_id, name)))
alice = agent.for_user("alice")
result = alice.run("总结我的私有项目")
```

不同 Agent 独立创建，再用容易阅读的 Python 代码组合：

```python
main = Agent("agents/main.toml")
coder = Agent("agents/coder.toml")
reviewer = Agent("agents/reviewer.toml")

main.add_subagent(coder, name="coder", triggers=["代码", "实现"])
main.add_subagent(reviewer, triggers=["审查"])
result = main.run("实现并审查这个改动")
```

省略子 Agent 名称时，会依次生成 `subagent01`、`subagent02`。多层或循环关系只产生
包含完整路径的警告，最终何时结束仍由 workflow 规则决定。

## 存储

默认后端是 `.super-agent/` 下可直接阅读、零依赖的 JSONL。切换 SQLite 只需修改一个
配置项，也不增加依赖；MySQL 和 PostgreSQL 则只安装实际使用的驱动。

所有后端保存同一种标准事件。项目 Skill 是只读基线，用户创建、编辑、安装和进化的
Skill 位于隔离的用户覆盖层，并按以下顺序解析：

```text
用户 > 项目 > 内置
```

后端配置见[配置说明](docs/configuration.md)，状态语义见
[Runtime 文档](docs/runtime.md)。

## 统一证明

当前维护的 [v0.0.61 统一 Runtime 证明](docs/experiments/v0.0.61.md)会在一个真实
Agent 上运行相互隔离的 Alice 与 Bob。它验证用户模型 Skill 与密钥、自动模型调度、
渐进披露缓存复用、记忆整理与遗忘、handler 执行前的安全阻断、不含密钥的标准事件与
运行锁，以及只对受影响用户发生的 Skill 自动晋升。

整个证明确定性、本地运行、只使用 Python 标准库，也不需要真实 API Key：

```bash
PYTHONPATH=src python3 docs/experiments/run_v0_0_61.py
```

[机器可读报告](docs/experiments/v0.0.61.json)中的七项检查全部通过。早期报告仅作为
[历史版本记录](docs/experiments/README.md)保留，不是当前 `0.0.x` API 的兼容目标。

## 文档

- [快速开始](docs/getting-started.md)
- [架构](docs/architecture.md)
- [Skill 与渐进披露](docs/skills.md)
- [Capability](docs/capabilities.md)
- [Runtime、追踪与多 Agent](docs/runtime.md)
- [评价、记忆与进化](docs/evolution.md)
- [Runtime 安全](docs/safety.md)
- [CLI 参考](docs/cli.md)
- [配置](docs/configuration.md)
- [Web 客户端](docs/web.md)
- [AG-UI 协议桥](docs/ag-ui.md)
- [版本路线图](docs/roadmap.md)

## 开发

```bash
PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .
python3 -m compileall -q src tests docs/experiments
pnpm --dir web lint
pnpm --dir web build
```

Python Runtime 没有第三方依赖。公共 API 从 `super_agent` 导出；在 `0.0.x` 阶段，
内部模块不会保留兼容 facade。
