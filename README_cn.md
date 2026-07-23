# Super Agent

[English](README.md)

> Skill is all you need.

Super Agent 是一个简单、轻量、配置化的 **skill-first agent runtime**，用于验证“全 Skill、全自动、全进化”的 Agent 架构是否可行。

- **全 Skill**：prompt、工具、记忆和 workflow 使用同一种 Skill 格式声明、组合和复用。
- **全自动**：Agent 按任务选择、披露并执行能力，应用层不需要重复编排。
- **全进化**：Skill 可以被评价、更新、晋升和回滚，运行数据会持续更新记忆与保鲜度。

项目当前处于 `0.0.x` 实验阶段，优先建立真实、可验证的执行闭环，不承诺 1.0 API 稳定性。

## 为什么使用 Super Agent

- **CLI 优先**：初始化、运行、检查和管理 Skill，适合本地使用、自动化和开源传播。
- **Python 内核**：CLI 只是入口；同一个 runtime 可以嵌入服务、脚本、Notebook、CI 或桌面应用。
- **中心化渐进披露**：所有 Skill 先进入统一索引，命中后才读取 manifest、指令和 Capability 配置。
- **代码式多 Agent**：每个 Agent 独立创建，再通过 `Agent.add_subagent(...)` 自然组合。
- **可追踪执行**：主 Agent、子 Agent、模型步骤和工具调用都写入统一运行事件。
- **轻依赖**：Python runtime 只使用标准库，便于安装、审计和二次开发。

## 快速开始

要求 Python 3.11 或更高版本。

```bash
python3 -m pip install -e .
super-agent
```

直接运行命令就会进入交互对话，自动使用内置 workflow、memory 和本地 `mock` provider；首次运行不需要配置、项目文件或 API key。单次调用同样直接：

```bash
super-agent run "hello"
```

Python 入口也无需配置：

```python
from super_agent import Agent

result = Agent().run("hello")
print(result.text)
```

只有需要修改模型或 Skill 时才初始化项目：

```bash
super-agent init --path demo-agent
super-agent run --config demo-agent/agent.toml "hello"
```

生成目录包含可编辑的 Agent 配置，以及 prompt、MCP、memory 和 workflow 示例 Skill。

不安装命令也可以直接从源码运行：

```bash
PYTHONPATH=src python3 -m cli init --path demo-agent
PYTHONPATH=src python3 -m cli run --config demo-agent/agent.toml "hello"
```

## 架构

Super Agent 明确划分五类职责：

- **Provider 提供智能**：只负责统一的模型调用协议。
- **Runtime 调度能力**：只依赖 Capability 契约，不持有具体 Skill 行为。
- **Capability 执行机制**：负责检索、执行、评价、更新和记录。
- **Skill 承载内容**：保存供 Capability 使用的内容与配置。
- **Agent 组合一切**：通过 `set_run_controller(...)`、`set_skill_retriever(...)`、`add_skill_executor(...)` 等直白方法组合和替换能力。

`Agent()` 会自动装配经过测试的默认能力。高级用户可以只替换一个 Capability，无需重写 Runtime，也不需要引入另一套配置系统。

常用检查命令：

```bash
super-agent skills list --config demo-agent/agent.toml
super-agent skills index --config demo-agent/agent.toml --output json
super-agent skills validate --config demo-agent/agent.toml
super-agent skills explain --config demo-agent/agent.toml --prompt "hello"
super-agent skills freshness --config demo-agent/agent.toml
super-agent memory habits --config demo-agent/agent.toml
```

## Agent 配置

`agent.toml` 只配置单个 Agent。主从关系不写入 TOML，而是在代码中组合。

```toml
[agent]
name = "super-agent"
system = "You are a concise, helpful agent."
workflow = "direct"
memory = "default"
skills = ["echo"]
use_features = ["skill"]
disable_names = []
# 可选。不配置表示无限深度；超过后只产生 warning，不会阻止执行。
max_agent_chain_depth = 5

[model]
provider = "mock"
model = "mock"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
```

`workflow` 和 `memory` 是 Skill 名称选择器。`paths.skills` 是所有 Capability 共用的递归扫描入口；`paths.memory` 保存运行数据，不保存 Skill 内容。

`use_features` 默认是 `["skill"]`。`disable_names` 可以关闭整个 Capability、稳定键或所有同名 Skill：

```toml
[agent]
disable_names = ["memory:default", "workflow:direct", "prompt:echo", "mcp"]
```

稳定身份统一使用 `capability:name`。不带 Capability 的 `echo` 会匹配所有同名 Skill。

### 模型 Provider

当前支持：

- `auto`：不探测网络，自动解析可用配置。
- `mock`：本地开发和测试，不发起网络请求。
- `openai-compatible`：调用 `<base_url>/chat/completions`。
- `anthropic-compatible`：调用 `<base_url>/v1/messages`。

```toml
[model]
provider = "auto"
model = "gpt-4.1-mini"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
```

将 `provider = "auto"` 且 `model` 留空即可自动解析。优先级依次是 `SUPER_AGENT_PROVIDER`、本地 `OLLAMA_HOST`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`，最后使用确定性的 `mock` provider。当前目录存在 `agent.toml` 时，`Agent()` 会自动读取；`SUPER_AGENT_CONFIG` 优先级更高。显式 TOML 值优先于自动发现。密钥只从 `api_key_env` 指定的环境变量读取，不写入配置文件。

可以在真正调用模型前查看解析结果：

```bash
super-agent models list
super-agent models resolve
```

命令只展示 provider、模型、来源、地址和环境变量是否就绪，不会输出密钥值。

## 统一 Skill 模型

每个 Skill 通过 `capability` 声明它需要的执行机制：

- `prompt`：给模型使用的指令。
- `mcp`：可发现、可调用的 MCP 工具。
- `memory`：记忆策略；记忆数据仍独立保存在运行目录。
- `workflow`：Agent 的执行方式和结束条件。

一个最小 prompt Skill：

```text
skills/prompt/echo/
  skill.toml
  SKILL.md
```

```toml
schema_version = 2
name = "echo"
capability = "prompt"
description = "Minimal example skill"
version = "0.1.0"
triggers = ["echo", "brief"]
agent_created = false
agent_can_update = false
freshness = 70
function_group = "general"
provides = ["echo"]
requires = []

[entry]
instructions = "SKILL.md"
```

`SKILL.md` 保存真正披露给模型的说明。纯配置 Skill 可以省略 `[entry]`。中心 parser 会自动发现任意 Capability 名；注册同名 Skill executor 后，新的模型侧 Capability 就能直接执行。

### 组合与依赖

`provides` 声明能力，`requires` 声明依赖能力。省略 `provides` 时默认提供与 Skill 同名的能力。runtime 会按拓扑顺序加载完整依赖；缺失、循环或多个提供者造成的歧义都会明确报错。

```toml
name = "research"
provides = ["facts"]
requires = ["http"]
```

```bash
super-agent skills graph --config agent.toml --name report
super-agent skills lock --config agent.toml --name report --output skill.lock
```

`skill.lock` 记录 Capability 名、版本、依赖边和目录 SHA-256，不记录时间或绝对路径，因此相同输入会生成逐字节一致的文件。

## 中心化渐进披露

`ProgressiveDisclosureCore` 是所有 Skill 的唯一读取入口。Agent、CLI、benchmark、依赖解析、进化、包管理和 macOS 前端都消费同一份中心索引；`skill.toml` 只由中心 source parser 解析。

每次运行按四个阶段披露：

1. `index`：写入所有未禁用 Skill 的摘要、保鲜度和缓存路径，不读取指令正文。
2. `manifest`：按需写入规范化 manifest，并通过 `capability:name` 区分同名 Skill。
3. `instructions`：只读取被选择 Skill 的 `SKILL.md`。
4. `configuration`：只在 Capability 需要时读取统一的 `[configuration]` 表。

默认缓存结构：

```text
.super-agent/memory/disclosure/
  index.json
  history.jsonl
  skills/<capability>/<name>/manifest.json
  skills/<capability>/<name>/instructions.md
  skills/<capability>/<name>/configuration.json
```

内容 SHA-256 没有变化时会复用原缓存文件和路径。每次披露都会追加到 `history.jsonl`；模型也可以直接调用 `read_disclosed_content` 读取索引中已给出的缓存路径。

核心 Python API：

- `prepare_skill_index()`：生成中心 Skill 索引。
- `select_skill_references_for_prompt(...)`：按配置、触发词和依赖选择 Skill。
- `open_skill(...)`：用名称和可选 Capability 打开分阶段披露句柄。
- `read_disclosed_content(...)`：读取已经披露的缓存内容。
- `read_disclosure_history()`：读取完整披露路径历史。

## Workflow 与真实工具循环

workflow 本身也是 Skill：

```toml
schema_version = 2
name = "react"
capability = "workflow"
description = "Tool-using workflow"
version = "0.1.0"
triggers = []

[configuration]
mode = "react"
max_steps = 8
instruction = "Finish as soon as the task is complete."
```

内置 `mode`：

- `direct`：一次模型请求。
- `plan`：先生成紧凑计划，再在同一次请求中执行。
- `react`：模型按需使用 runtime 工具，未继续请求工具时结束。
- `loop`：持续使用 runtime 工具，直到模型结束或达到 `max_steps`。

`react` 和 `loop` 可以调用统一工具协议：

- `list_skills`、`read_skill_manifest`、`read_skill_instructions`、`read_skill_configuration`。
- `read_disclosed_content`、`list_skill_tools`、`run_skill`。
- `list_memory_items`、`add_memory_item`、`recall_memory`、`forget_memory`、`consolidate_memory`。
- `list_subagents`、`run_subagent`。

## 代码式多 Agent

每个 Agent 使用自己的配置、模型、Skill、memory 和 workflow，再通过清晰的代码关系挂到主 Agent 下：

```python
from super_agent import Agent

main = Agent.load_from_config_file("agents/main.toml")
coder = Agent.load_from_config_file("agents/coder.toml")
reviewer = Agent.load_from_config_file("agents/reviewer.toml")

main.add_subagent(
    coder,
    name="coder",
    description="Implement code and tests",
    triggers=["code", "implement", "test"],
)
main.add_subagent(reviewer, description="Review risks and boundaries", triggers=["review"])

result = main.run("Implement a lightweight agent feature and review it")
print(result.text)
```

`name` 可选；省略时依次生成 `subagent01`、`subagent02`。显式名称不能重复。动态创建的子 Agent 可以传 `created_by_agent=True`，运行结果会保留该标记和完整嵌套结果，供前端展示运行树。

`direct` 和 `plan` 在触发条件满足时预先运行子 Agent；未设置 `triggers` 的子 Agent 每次都会运行。`react` 和 `loop` 则把 `list_subagents` 与 `run_subagent` 提供给模型，由模型决定何时委派及使用什么 prompt。

子 Agent 可以继续添加子 Agent。执行前默认检查链路，但只返回 warning：

- 超过 `max_agent_chain_depth` 时提示具体层数和链路。
- 发现循环时提示完整链路，例如 `main -> coder -> reviewer -> main`。
- 未配置最大深度时不限制嵌套；runtime 不强制停止，结束条件由 workflow 决定。

也可以主动调用 `main.check_subagent_links()` 获取这些提示。

## 运行追踪

每次 `Agent.run(...)` 都生成独立 `run_id`，事件按顺序写入：

```text
.super-agent/memory/runs/<run-id>/events.jsonl
```

子 Agent 使用自己的 `run_id`，并通过 `parent_run_id` 指向父运行。事件覆盖 Skill 披露、模型步骤、工具调用、记忆更新和运行结果，因此调用方可以还原完整执行树。

```bash
super-agent run --output json --config agent.toml "hello"
```

桌面端或其他进程可以使用 JSONL 协议：

```bash
printf '%s' '{"prompt":"hello","messages":[]}' \
  | super-agent run --config agent.toml --request-stdin --output jsonl
```

每一行的 `type` 是 `event` 或 `result`。

## 记忆自更新

memory Skill 定义策略，用户记忆与调用习惯保存在运行目录：

```toml
schema_version = 2
name = "default"
capability = "memory"
description = "Default memory behavior"
version = "0.1.0"
triggers = []

[configuration]
default_scope = "agent"
recall_limit = 20
include_in_prompt = true
include_usage_habits = true
```

```text
.super-agent/memory/
  memory_events.jsonl
  habits.json
```

记忆事件只追加，不删除历史证据。召回先按 scope 隔离，再按中英文词法相关度排序；归并只合并同一 scope 中规范化后完全相同的内容。每次成功运行还会更新 workflow 和 Skill 使用习惯，并在后续运行中按策略加入上下文。

```bash
super-agent memory habits --config agent.toml
super-agent memory list --config agent.toml --scope agent
super-agent memory add --config agent.toml --text "Prefer concise answers." --scope agent
super-agent memory recall --config agent.toml --query "answer style" --scope agent --limit 5
super-agent memory forget --config agent.toml --item-id <memory-id>
super-agent memory consolidate --config agent.toml
```

## Skill 保鲜度

保鲜度不调用大模型打分。runtime 将 Skill 调用写入 `skill_events.jsonl`，并把聚合结果写入 `skill_stats.json`。评分综合以下可解释信号：

- `quality`：近期成功率 EWMA。
- `recency`：距上次调用的时间，按 7 天半衰期衰减。
- `frequency`：调用次数和调用频率。
- `efficiency`：粗略输入/输出 token 成本。
- `reliability`：成功率和空输出率。
- `replacement`：调用后是否很快改用同 `function_group` 的其他 Skill 且后者成功。
- `confidence`：样本少时回归默认值，减轻冷启动误判。

```bash
super-agent skills freshness --config agent.toml
```

中心索引和 macOS 前端都会展示动态保鲜度及其统计。

## Skill 进化闭环

Skill manifest 使用两个字段控制进化权限：

- `agent_created`：是否由 Agent 创建。
- `agent_can_update`：是否允许 Agent 更新；省略时默认等于 `agent_created`。

默认情况下，人类创建的 Skill 不允许自动更新，Agent 自己创建的 Skill 可以继续优化。更新不会直接覆盖在线文件：runtime 先创建隔离候选，使用真实 provider 运行确定性评价，通过阈值且父版本未变化后才原子晋升。每次晋升都会保存不可变快照，可随时回滚。

```python
from super_agent import Agent, EvaluationCase

manager = Agent.load_from_config_file("agent.toml").create_skill_evolution_manager()
candidate = manager.create_skill_candidate("research-note", "Summarize with sources")
report = manager.evaluate_skill_candidate(
    candidate.candidate_id,
    [
        EvaluationCase(
            name="contains source",
            prompt="Summarize this note.",
            expected_output_contains=["source"],
            forbidden_output_contains=["unknown"],
        )
    ],
)
if report.passed:
    manager.promote_skill_candidate(candidate.candidate_id)

manager.rollback_skill("research-note")
```

评价使用显式字符串断言，不额外调用模型充当裁判：

```json
[
  {
    "name": "contains source",
    "prompt": "Summarize this note.",
    "expected_output_contains": ["source"],
    "forbidden_output_contains": ["unknown"],
    "evaluator_instruction": "Return a concise answer."
  }
]
```

```bash
super-agent skills propose --config agent.toml --name research-note --goal "summarize with sources"
super-agent skills evaluate --config agent.toml --candidate-id <id> --cases evaluation-cases.json
super-agent skills promote --config agent.toml --candidate-id <id>
super-agent skills evolve --config agent.toml --name research-note --goal "make it clearer" --cases evaluation-cases.json
super-agent skills rollback --config agent.toml --name research-note
```

候选、评价报告和历史默认写入 `.super-agent/memory/evolution/`。

## MCP Skill

MCP 也是普通 Skill，使用 `capability = "mcp"`：

```toml
schema_version = 2
name = "filesystem"
capability = "mcp"
description = "Example stdio MCP server"
version = "0.1.0"
triggers = ["filesystem", "files"]

[entry]
instructions = "SKILL.md"

[configuration]
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem"]

[configuration.env]
ROOT_PATH = "/tmp"
```

当前 runtime 执行 stdio MCP。环境变量值只传给 MCP 子进程，模型上下文只看到变量名。`react` 和 `loop` 先用 `list_skill_tools` 发现工具，再用 `run_skill` 调用。

## Skill 包管理

Skill 可以打包为确定性 ZIP，也可以从本地目录、ZIP 或 Git 仓库安装：

```bash
super-agent skills pack --config agent.toml --name research --output research.zip
super-agent skills install --config agent.toml --source ./research.zip
super-agent skills install --config agent.toml --source 'git+https://github.com/example/skills.git#skills/research'
super-agent skills update --config agent.toml --name research --source ./new-research
super-agent skills remove --config agent.toml --name research
```

`--expected-sha256` 校验规范化 Skill 目录内容哈希，与 `skill.lock` 一致。安装会拒绝符号链接、ZIP 路径穿越、名称冲突和哈希不一致；更新在完整校验后切换目录，失败时恢复旧版本。

## 渐进披露基准

基准比较“能力索引 + 全量指令”和“能力索引 + 命中指令”的上下文成本，不调用模型：

```bash
super-agent benchmark \
  --config examples/basic/agent.toml \
  --cases examples/basic/benchmark-cases.json \
  --output report.json
```

报告包含 `eager_context_tokens`、`progressive_context_tokens`、`saved_context_tokens` 和 `context_savings_ratio`。token 使用 `ceil(字符数 / 4)` 的固定近似值，适合在提交间做可复现比较，不代表特定模型 tokenizer 的账单。

## Python API

第三方统一从 `super_agent` 导入公共 API：

```python
from super_agent import Agent, AgentConfig

config = AgentConfig.load_from_file("agent.toml")
agent = Agent(config)
result = agent.run("Summarize this project")

print(result.text)
```

常用入口：

- `Agent.load_from_config_file(...)`、`Agent.run(...)`、`Agent.add_subagent(...)`。
- `Agent()`、`AgentConfig.load_automatically(...)`：自动读取项目配置并零配置启动。
- `resolve_model_settings(...)`、`discover_model_candidates(...)`：执行确定性的模型解析。
- `Agent.list_subagents()`、`Agent.check_subagent_links()`。
- `Agent.create_skill_evolution_manager()`。
- `ProgressiveDisclosureCore.prepare_skill_index()`、`open_skill(...)`。
- `MiniMemory.add_memory_item(...)`、`recall_memory(...)`、`forget_memory(...)`、`consolidate_memory()`。
- `SkillBenchmark.run_cases(...)`。
- `run_event_to_dict(...)`、`run_event_from_dict(...)`、`skill_manifest_to_dict(...)`。

项目内部直接导入定义模块，不通过中间 facade；`src/super_agent.py` 是唯一公共聚合入口。

## Schema 与兼容性

Skill manifest 必须显式设置 `schema_version = 2`，并提供 `name`、`capability`、`description`、`version` 和 `triggers`。`[entry]` 可选，用来指向说明文件；任意 Capability 的设置都统一写入 `[configuration]`。v1 与旧的 Capability 专用配置表会被直接拒绝，不做隐式转换。

运行事件 v1 固定包含 `schema_version`、`run_id`、`sequence`、`event_type`、`created_at`、`agent_name`、`parent_run_id` 和 `data`。读取器拒绝缺失字段、未知字段、错误类型和不支持的 schema 版本，不做静默兼容。

`0.0.x` 阶段的 Python API 仍可能调整。持久化格式发生不兼容变化时会要求显式迁移，不会把旧数据猜测成新格式。当前 provider 名称只接受 `mock`、`openai-compatible` 和 `anthropic-compatible`。

## macOS 前端

`src/frontend/mac` 提供 SwiftUI 桌面端，包含对话管理、TOML 可视化配置、模型列表、Skill 开关与保鲜度、默认 memory/workflow 切换，以及主从 Agent 运行树。

```bash
cd src/frontend/mac
swift run SuperAgentMac
```

前端通过 JSONL 协议调用 Python runtime，并通过 `skills index --output json` 读取中心索引，不自行解析 Skill manifest。详细说明见 `src/frontend/mac/README.md`。

## 项目结构

```text
src/
  agents/        # 代码式 Agent 组合
  capability/    # 可替换的执行机制
  provider/      # 模型 Provider 适配
  runtime/       # 仅依赖 Capability 的调度、配置、事件和结果
  cli.py         # 零配置 CLI 入口
  cli_commands/  # 按领域组织的高级命令
  builtin_skills/ # 零配置 workflow 与 memory 内容
  skill/         # 统一 Skill 内容模型
    disclosure/  # Central parser, index, cache, and history
    ecosystem/   # Dependency lock and package management
    evolution/   # Candidates, evaluation, freshness, and history
    kinds/       # MCP, memory, and workflow implementations
  frontend/mac/  # SwiftUI desktop app
```

## 开发与验证

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m compileall -q src tests
```

## 设计原则

- Runtime 只调度配置的 Capability 契约，不导入具体实现。
- Agent 通过代码式 API 组合 Provider、Runtime、Capability、Skill 和子 Agent。
- Skill 是唯一能力扩展边界，不为 prompt、工具、记忆或 workflow 建立平行系统。
- 配置描述单个 Agent；代码负责清晰、灵活的 Agent 关系。
- 默认错误必须明确，不使用隐式 fallback 或兼容薄壳掩盖问题。
- 自动进化必须经过隔离候选、真实评价、条件晋升和可回滚历史。
- 新能力先完成最小真实闭环，再沉淀为稳定 schema。
