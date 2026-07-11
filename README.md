# Super Agent

Skill is all you need.

Super Agent 是一个简单、轻量、配置化的 **skill-first agent runtime**。

全Skill、全自动、全进化

具体的：
1. 全Skill：将 agent 能力拆成可渐进加载的 skill：prompt、工具、记忆、workflow 都可以用同一套格式声明、组合和复用。
2. 全自动：agent自动控制一切。
3. 全进化：所有依赖均可进化。

验证全skill化agent的可行性。

## 核心定位

- **CLI 优先**：直接初始化、运行、管理 skill，适合真实使用和开源传播。
- **Python 库内核化**：CLI 只是一层入口，第三方可以把同一个 runtime 嵌进服务、脚本、Notebook、CI 或桌面应用。
- **渐进式 skill**：先看索引，再读 manifest，命中后才加载 `SKILL.md` 和资源，避免一次性塞满上下文。
- **轻依赖**：首版只使用 Python 标准库，方便审计、安装和二次开发。

## 快速开始

```bash
PYTHONPATH=src python3 -m cli init --path demo-agent
PYTHONPATH=src python3 -m cli run --config demo-agent/agent.toml "hello"
PYTHONPATH=src python3 -m cli skills list --config demo-agent/agent.toml
PYTHONPATH=src python3 -m cli skills index --config demo-agent/agent.toml --output json
PYTHONPATH=src python3 -m cli skills validate --config demo-agent/agent.toml
PYTHONPATH=src python3 -m cli skills freshness --config demo-agent/agent.toml
PYTHONPATH=src python3 -m cli memory habits --config demo-agent/agent.toml
PYTHONPATH=src python3 -m cli benchmark --config examples/basic/agent.toml --cases examples/basic/benchmark-cases.json
```

安装为命令后：

```bash
super-agent init --path demo-agent
super-agent run --config demo-agent/agent.toml "hello"
super-agent skills list --config demo-agent/agent.toml
super-agent skills index --config demo-agent/agent.toml --output json
super-agent skills validate --config demo-agent/agent.toml
super-agent skills freshness --config demo-agent/agent.toml
super-agent memory habits --config demo-agent/agent.toml
super-agent benchmark --config examples/basic/agent.toml --cases examples/basic/benchmark-cases.json
```

默认配置使用 `mock` provider，不需要 API key，方便本地验证。

## Python API

```python
from super_agent import Agent, AgentConfig

config = AgentConfig.load_from_file("agent.toml")
agent = Agent(config)
result = agent.run("总结这个项目的设计")

print(result.text)
```

常用方法名尽量直白：

- `AgentConfig.load_from_file(path)`：从 TOML 文件读取配置。
- `Agent.load_from_config_file(path)`：从配置文件创建 agent。
- `Agent.add_subagent(agent, ...)`：给主 agent 添加从 agent。
- `Agent.list_subagents()`：查看已经添加的从 agent。
- `ProgressiveDisclosureCore.prepare_skill_index()`：生成全部 Skill 类型共用的中心索引。
- `ProgressiveDisclosureCore.select_skill_references_for_prompt(...)`：按配置、触发词和依赖选择 Skill。
- `ProgressiveDisclosureCore.open_skill(...)`：按 `kind:name` 打开一个分阶段披露句柄。
- `ProgressiveDisclosureCore.read_disclosed_content(path)`：读取中心缓存已经披露的内容。
- `ProgressiveDisclosureCore.read_disclosure_history()`：读取完整披露路径历史。
- `Agent.create_skill_evolution_manager()`：创建候选、评价、晋升和回滚使用的进化管理器。
- `SkillEvolutionManager.create_skill_candidate(...)`：生成隔离候选，不修改当前 Skill。
- `SkillEvolutionManager.evaluate_skill_candidate(...)`：用真实 provider 运行用例并确定性评分。
- `SkillEvolutionManager.promote_skill_candidate(...)`：只晋升通过评价且父版本未变化的候选。
- `SkillEvolutionManager.evolve_skill(...)`：一次完成候选、评价和条件晋升。
- `SkillEvolutionManager.rollback_skill(...)`：恢复上一份不可变 Skill 快照。
- `MiniMemory.add_memory_item(...)`：追加带作用域和来源运行编号的记忆事件。
- `MiniMemory.recall_memory(...)`：按作用域和词法相关度召回记忆。
- `MiniMemory.forget_memory(...)`：追加遗忘事件，不破坏历史记录。
- `MiniMemory.consolidate_memory()`：确定性合并重复记忆，不调用模型。
- `MemoryUsageHabits.record_agent_run(...)`：独立更新 workflow 和 Skill 使用习惯。
- `SkillBenchmark.run_cases(...)`：确定性比较全量加载与渐进披露的上下文用量。
- `run_event_to_dict(...)` / `run_event_from_dict(...)`：按公开 schema v1 序列化运行事件。
- `skill_manifest_to_dict(...)`：输出不含本机路径的规范化 Skill manifest。

## 代码式多 Agent

主从关系用代码组合，不写进 TOML。每个 agent 都是普通 `Agent`，各自有独立配置、模型、skill、memory、workflow。

```python
from super_agent import Agent

main = Agent.load_from_config_file("agents/main.toml")
coder = Agent.load_from_config_file("agents/coder.toml")
reviewer = Agent.load_from_config_file("agents/reviewer.toml")

main.add_subagent(coder, name="coder", description="实现代码和测试", triggers=["代码", "实现", "测试"])
main.add_subagent(reviewer, description="审查风险和边界", triggers=["review", "风险"])

result = main.run("实现一个轻量的 agent 功能并 review")
print(result.text)
```

`name` 可选。不传时会自动生成 `subagent01`、`subagent02` 这样的稳定名称；显式名称不能重复。`direct` 和 `plan` workflow 保持触发式委派：带 `triggers` 时只在命中后运行，不带时每次运行都会调用。主 agent 会把结果作为 `Subagent results` 加入最终回答上下文。

`react` 和 `loop` workflow 不预先运行子 agent，而是向模型提供 `list_subagents` 和 `run_subagent`。模型可以先查看代码挂载的能力，再按名字和新 prompt 选择委派对象。子运行拥有独立 `run_id`，其首个事件通过 `parent_run_id` 指向主运行；结果也会进入同一棵运行树。

如果子 agent 是由 agent 动态创建的，挂载时传 `created_by_agent=True`。运行结果会保留这个标记，并把嵌套子 agent 结果作为树返回，方便前端展示运行对话。

从 agent 可以继续添加自己的从 agent。runtime 不做安全阀，不会因为嵌套层数或循环链路强行停止；每个 agent 是否结束由自己的 workflow 决定。执行前只做一次轻量链路检查：

- 如果配置了 `max_agent_chain_depth`，链路深度超过配置时返回 warning，例如 `main -> coder -> reviewer`。
- 如果发现循环链路，会返回 warning，例如 `main -> coder -> reviewer -> main`。
- 不配置 `max_agent_chain_depth` 时默认无限深度。

代码里也可以主动检查：

```python
for warning in main.check_subagent_links():
    print(warning)
```

## 配置格式

```toml
[agent]
name = "super-agent"
system = "You are a concise, helpful agent."
workflow = "direct"
memory = "default"
skills = ["echo"]
use_features = ["skill"]
disable_names = []
# 可选；不配置表示无限深度。超过后只 warning，不阻止执行。
max_agent_chain_depth = 5

[model]
provider = "mock"
model = "mock"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
```

`workflow` 和 `memory` 是名称选择器，分别选择 `kind = "workflow"` 和 `kind = "memory"` 的 skill。`paths.memory` 只表示记忆数据目录，不表示记忆能力目录。

`use_features` 控制是否扫描统一技能树，默认等于 `["skill"]`。`disable_names` 是批量关闭列表，可以写 kind，也可以写具体 skill 名：

```toml
[agent]
disable_names = ["memory:default", "workflow:direct", "prompt:echo", "mcp:github"]
```

不带前缀的名字会同时匹配所有 skill kind，例如 `disable_names = ["github"]` 会关闭名为 `github` 的注册项。

当前 provider：

- `mock`：本地测试用。
- `openai-compatible` / `openai`：请求 `/chat/completions`。
- `anthropic-compatible` / `anthropic`：请求 `/v1/messages`。

## Skill 格式

一个 skill 是一个目录：

```text
skills/echo/
  skill.toml
  SKILL.md
```

`skill.toml`：

```toml
schema_version = 1
name = "echo"
kind = "prompt"
description = "Minimal example skill"
version = "0.1.0"
agent_created = false
agent_can_update = false
freshness = 70
function_group = "general"
provides = ["echo"]
requires = []
triggers = ["echo", "brief"]

[entry]
instructions = "SKILL.md"
```

`SKILL.md` 存放真正给 agent 的说明。runtime 会根据配置和触发词选择 skill，并把命中的说明注入本次运行。

### 实验契约 v1

`v0.0.16` 冻结了首个实验性 schema v1，`v0.0.17` 将所有读取入口收敛到中心披露核心，但这不等于承诺 1.0 API 稳定性。Skill manifest 必须显式声明 `schema_version = 1`，并提供字符串字段 `name`、`kind`、`description`、`version` 和字符串数组 `triggers`。`prompt`、`mcp` 还必须提供 `[entry]`；`memory`、`workflow` 的行为入口是各自同名配置表。

运行事件 v1 固定为 `schema_version`、`run_id`、`sequence`、`event_type`、`created_at`、`agent_name`、`parent_run_id` 和 `data` 八个字段。读取器拒绝缺失字段、未知字段、错误类型和其他 schema 版本，不做静默兼容；升级时应先显式迁移数据。公共序列化入口为 `skill_manifest_to_dict(...)`、`run_event_to_dict(...)` 和 `run_event_from_dict(...)`。

在 `0.0.x` 阶段，Python 方法仍可能调整；持久化格式发生不兼容变化时会使用新的 schema 版本和明确迁移错误，不会把旧数据猜测成新格式。

`provides` 声明能力，`requires` 声明依赖能力。未写 `provides` 时默认提供与 Skill 名相同的能力。配置一个 Skill 后，runtime 会按依赖拓扑自动加载其全部依赖；缺失、循环或多个提供者造成的歧义都会直接报错。

```toml
name = "research"
provides = ["facts"]
requires = ["http"]
```

可以查看解析结果并生成可复现 lock：

```bash
super-agent skills graph --config agent.toml --name report
super-agent skills lock --config agent.toml --name report --output skill.lock
```

`skill.lock` 按 Skill 名排序，记录 kind、版本、能力边和目录 SHA-256，不写本机绝对路径。同一组内容和输入会生成完全一致的文件。

### Skill 包管理

Skill 可以打包为确定性 ZIP，也可以从本地目录、ZIP 或 Git 仓库安装：

```bash
super-agent skills pack --config agent.toml --name research --output research.zip
super-agent skills install --config agent.toml --source ./research.zip
super-agent skills install --config agent.toml --source 'git+https://github.com/example/skills.git#skills/research'
super-agent skills update --config agent.toml --name research --source ./new-research
super-agent skills remove --config agent.toml --name research
```

Git 来源格式为 `git+<repository>#<optional-subdirectory>`，克隆时禁用交互式凭据提示。`--expected-sha256` 可用于 install/update，校验规范化 Skill 目录内容哈希，与 `skill.lock` 的 `sha256` 一致，不是 ZIP 容器本身的哈希：

```bash
super-agent skills install \
  --config agent.toml \
  --source ./research.zip \
  --expected-sha256 <sha256-from-skill-lock>
```

打包固定文件顺序、时间戳和权限。安装会先在临时目录完整校验，拒绝符号链接、ZIP 路径穿越、名称冲突和哈希不一致；更新通过同目录重命名切换，失败时恢复旧目录。

`kind` 是唯一分类依据：

- `prompt`：普通提示词 skill，读取 `SKILL.md`。
- `mcp`：MCP 工具 skill，读取 `[mcp]`。
- `memory`：记忆行为 skill，读取 `[memory]`，数据仍写入 `[paths].memory`。
- `workflow`：执行流程 skill，读取 `[workflow]`。

### Skill 保鲜度

skill 可以声明静态保鲜度字段：

```toml
freshness = 70
function_group = "research"
freshness_updated_at = "2026-07-07T12:00:00Z"
```

runtime 不用大模型给保鲜度打分，而是把每次调用写入动态统计：

```text
.super-agent/memory/
  skill_events.jsonl   # 每次 skill 调用事件
  skill_stats.json     # 聚合后的保鲜度、调用次数、替代统计
```

当前保鲜度由多个可解释信号综合计算：

- `quality`：近期成功率 EWMA。
- `recency`：距离上次调用时间，按 7 天半衰期衰减。
- `frequency`：调用频率，避免长期不用的 skill 虚高。
- `efficiency`：粗略输入输出 token 成本。
- `reliability`：成功率、空输出率。
- `replacement`：调用后短时间内是否又调用同 `function_group` 的其他 skill，且后者成功。
- `confidence`：调用次数少时回归默认值，避免冷启动误判。

查看动态统计：

```bash
super-agent skills freshness --config agent.toml
```

### Skill 评价进化闭环

每个普通 skill 都可以标记两个字段：

- `agent_created`：是否由 agent 自己创建。
- `agent_can_update`：是否允许 agent 更新；不写时默认等于 `agent_created`。

人类写的旧 Skill 默认不可进化；agent 新建的 Skill 默认允许继续进化。允许进化也不会直接改线上文件：模型先生成隔离候选，真实运行评价用例，达到阈值后才原子切换当前目录。

```python
from super_agent import Agent, EvaluationCase

manager = Agent.load_from_config_file("agent.toml").create_skill_evolution_manager()
candidate = manager.create_skill_candidate("research-note", "summarize with sources")
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

评价用例是普通 JSON，断言直接检查模型输出，不再额外调用一个模型充当裁判：

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

CLI 提供同一套闭环操作：

```bash
super-agent skills propose --config agent.toml --name research-note --goal "summarize with sources"
super-agent skills evaluate --config agent.toml --candidate-id <id> --cases evaluation-cases.json
super-agent skills promote --config agent.toml --candidate-id <id>
super-agent skills evolve --config agent.toml --name research-note --goal "make it clearer" --cases evaluation-cases.json
super-agent skills rollback --config agent.toml --name research-note
```

候选、评价报告和不可变历史默认写在 `.super-agent/memory/evolution/`。候选创建后如果当前 Skill 被人工修改，晋升会因父快照哈希不一致而拒绝，避免覆盖新修改。

## MCP Skill 格式

MCP 也是一种 skill，放在统一 `skills/` 目录下：

```text
skills/mcp/filesystem/
  skill.toml
  SKILL.md
```

`skill.toml`：

```toml
schema_version = 1
name = "filesystem"
kind = "mcp"
description = "Example stdio MCP server"
version = "0.1.0"
triggers = ["filesystem", "files"]

[entry]
instructions = "SKILL.md"

[mcp]
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem"]

[mcp.env]
ROOT_PATH = "/tmp"
```

runtime 会按 `kind = "mcp"` 把它加载成 MCP skill。`react` 和 `loop` workflow 可以通过 `list_skill_tools` 查看工具，再通过 `run_skill` 完成真实的 MCP stdio 调用。环境变量值只传给 MCP 子进程，模型上下文只看到变量名。

## 动态渐进式披露

每次运行都会建立一个披露缓存，默认位置：

```text
.super-agent/memory/disclosure/
  index.json
  history.jsonl
  skills/<kind>/<name>/manifest.json
  skills/<kind>/<name>/instructions.md
  skills/<kind>/<name>/configuration.json
```

披露流程：

1. `index` 阶段写入全部未禁用 Skill 的摘要、保鲜度和各阶段缓存路径，不读取指令正文。
2. `manifest` 阶段按需写入规范化 manifest，并用 `kind:name` 区分同名类型。
3. `instructions` 与 `configuration` 阶段分别披露 `SKILL.md` 和类型配置；内容 SHA-256 未变化时直接命中缓存。
4. 每次披露都会追加到 `history.jsonl`；模型可调用 `read_disclosed_content` 直接读取索引给出的缓存路径。

Agent、CLI、benchmark、依赖解析、进化、包管理和 macOS 前端都消费这个索引；Skill TOML 只由中心 source parser 解析。模型先看索引，再选择需要展开的 Skill，而不是一开始读完所有资源。

`v0.0.17` 不兼容删除 `SkillLoader`、旧 `ProgressiveDisclosure`、`SkillDependencyResolver`、`SkillManifest.load_from_file()` 和 `read_skill` 工具。库调用改用 `ProgressiveDisclosureCore`；工具调用改用 `read_skill_manifest`、`read_skill_instructions`、`read_skill_configuration` 和 `read_disclosed_content`。保鲜度统计键也统一为 `kind:name`，旧的裸名称统计不再读取。

### 渐进披露基准

两侧都计入同一份能力索引；全量侧再加载全部 prompt/MCP 指令，渐进侧只展开命中指令：

```bash
super-agent benchmark \
  --config examples/basic/agent.toml \
  --cases examples/basic/benchmark-cases.json
```

用例文件是 JSON 数组，每项包含唯一 `name`、`prompt`，并可用 `enabled_skills` 指定需要先解析依赖的 Skill。报告输出每个用例及总计的 `eager_context_tokens`、`progressive_context_tokens`、`saved_context_tokens` 和 `context_savings_ratio`；使用 `--output report.json` 可保存结果。

基准不调用模型，报告也不包含本机时间或本地路径。token 使用 `ceil(字符数 / 4)` 的固定近似值，因此适合在提交之间做可复现回归比较，不代表特定模型 tokenizer 的账单；负节省表示当前 Skill 集很小，索引成本高于省下的指令上下文。

## 运行追踪

每次 `Agent.run(...)` 都会生成稳定的 `run_id`，并把有序事件写入：

```text
.super-agent/memory/runs/<run-id>/events.jsonl
```

事件包含 schema 版本、顺序号、agent 名称、父运行编号和事件数据。子 agent 使用自己的运行编号，并通过 `parent_run_id` 关联主运行。

```bash
super-agent run --output json --config agent.toml "hello"
super-agent skills validate --config agent.toml
super-agent skills explain --config agent.toml --prompt "hello"
```

CLI 也提供给桌面端和其他进程使用的统一 JSONL 运行协议。请求从标准输入读取，运行事件逐行输出，最后一行是完整结果：

```bash
printf '%s' '{"prompt":"hello","messages":[]}' \
  | super-agent run --config agent.toml --request-stdin --output jsonl
```

输出行的 `type` 为 `event` 或 `result`。主 agent 和子 agent 的结果都带有真实 `run_id`，因此调用方可以直接还原运行树，不需要重复实现 provider 或 workflow。

## 记忆自更新

记忆也是一种 skill：

```text
skills/memory/default/
  skill.toml
```

```toml
schema_version = 1
name = "default"
kind = "memory"
description = "Default memory behavior"
version = "0.1.0"
triggers = []

[memory]
default_scope = "agent"
recall_limit = 20
include_in_prompt = true
include_usage_habits = true
```

Skill manifest 只声明记忆策略，用户数据单独保存在：

```text
.super-agent/memory/
  memory_events.jsonl  # 添加、遗忘和归并的只追加事件
  habits.json          # runtime 自动更新的调用习惯
```

每条记忆都有 `item_id`、`scope`、`source_run_id` 和创建时间。`recall_memory` 先按作用域隔离，再用英文词、数字和中文字符做确定性词法排序。`consolidate_memory` 只合并同一作用域内规范化后完全相同的内容。

在 `react` 或 `loop` workflow 中，模型可以直接调用 `list_memory_items`、`add_memory_item`、`recall_memory`、`forget_memory` 和 `consolidate_memory`。工具写入会自动记录当前 `run_id`，并同时进入运行事件追踪。

每次成功运行后，runtime 会记录：

- 总运行次数。
- 使用过的 workflow。
- 命中的 skill。

下一次运行时，相关记忆和这些习惯会加入系统上下文。CLI 提供完整操作：

```bash
super-agent memory habits --config agent.toml
super-agent memory list --config agent.toml --scope agent
super-agent memory add --config agent.toml --text "Prefer concise answers." --scope agent
super-agent memory recall --config agent.toml --query "answer style" --scope agent --limit 5
super-agent memory forget --config agent.toml --item-id <memory-id>
super-agent memory consolidate --config agent.toml
```

## Workflow Skill 格式

workflow 也是一种 skill：

```text
skills/workflow/direct/
  skill.toml
```

```toml
schema_version = 1
name = "direct"
kind = "workflow"
description = "Direct workflow"
version = "0.1.0"
triggers = []

[workflow]
mode = "direct"
```

内置 `mode` 当前支持 `direct`、`plan`、`react`、`loop`。也可以通过 `instruction` 给 workflow 增加额外提示：

```toml
[workflow]
mode = "plan"
instruction = "Prefer short numbered steps."
```

`direct` 和 `plan` 保持单次模型请求。`react` 和 `loop` 使用真实工具循环，模型可以调用：

- `list_skills`：查看可用 Skill 摘要。
- `read_skill_manifest`：按需披露一个 Skill 的 manifest。
- `read_skill_instructions`：按需披露一个 Skill 的指令。
- `read_skill_configuration`：按需披露 MCP、记忆或工作流配置。
- `read_disclosed_content`：读取中心索引中已经给出的缓存路径。
- `list_skill_tools`：读取 MCP 暴露的工具。
- `run_skill`：执行一个 MCP 工具。
- `list_subagents`：查看代码挂载在当前 agent 下的子 agent。
- `run_subagent`：按名字和独立 prompt 运行一个子 agent。

循环在模型不再请求工具时结束，也可以在 workflow Skill 中限制步数：

```toml
[workflow]
mode = "react"
max_steps = 8
```

## 架构

```text
src/
  cli.py         # CLI 入口：init、run、skills、memory
  cli_commands/  # 按领域拆分的 CLI 参数和命令适配
  core/          # Agent、运行追踪、真实 Skill 工具、agent.toml、provider
  skill/         # manifest、中心渐进式披露、kind、候选评价、进化历史
    disclosure/  # 唯一 source parser、中心索引、阶段缓存与披露历史
    ecosystem/   # 确定性 lock 与 Skill 包管理
    kinds/       # prompt、mcp、memory、workflow 等 skill kind
  frontend/mac/  # SwiftUI 桌面端：对话、配置、运行树
```

内核只保留 transport、运行事件和 Skill 装配机制。`direct`、`plan`、`react`、`loop` 都由 workflow Skill 声明，其中 `react` 和 `loop` 已通过统一工具协议执行多步模型循环。

`memory` 采用 JSONL 事件存储，实现在 `skill/kinds/memory.py`。策略由 memory Skill 声明，数据不和能力定义混放。

macOS 前端通过上述 JSONL 协议调用同一个 Python runtime，并通过 `skills index --output json` 获取全部配置选项和保鲜度；Swift 不扫描 Skill manifest。会话保存成 JSON，并按真实 `run_id` 生成类似文件树的运行节点。点击主 agent 或子 agent 节点，可以查看对应输入与输出。

## 开发验证

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## 设计原则

- 内核只负责装配和运行，不持有业务 prompt。
- skill 是扩展边界，不是写死在代码里的分支。
- prompt、mcp、memory、workflow 都用 `skill.toml` 和 `kind` 分类。
- 默认失败要清楚，不用隐式 fallback 掩盖配置错误。
- 新能力先用最小 API 跑通，再沉淀成稳定 schema。
