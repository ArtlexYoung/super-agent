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
PYTHONPATH=src python3 -m cli skills create --config demo-agent/agent.toml --name note --instructions "Write concise notes."
PYTHONPATH=src python3 -m cli skills freshness --config demo-agent/agent.toml
PYTHONPATH=src python3 -m cli memory habits --config demo-agent/agent.toml
```

安装为命令后：

```bash
super-agent init --path demo-agent
super-agent run --config demo-agent/agent.toml "hello"
super-agent skills list --config demo-agent/agent.toml
super-agent skills create --config demo-agent/agent.toml --name note --instructions "Write concise notes."
super-agent skills freshness --config demo-agent/agent.toml
super-agent memory habits --config demo-agent/agent.toml
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
- `SkillLoader.list_skill_manifests()`：列出 skill 配置摘要。
- `SkillLoader.load_skills_for_prompt(prompt)`：按输入加载命中的 skill。
- `Agent.create_skill(...)`：让 agent 创建一个自己拥有的 skill。
- `Agent.update_skill(...)`：更新允许 agent 更新的 skill。
- `Agent.optimize_skill(...)`：用当前模型优化允许更新的 skill。
- `MiniMemory.record_agent_run(...)`：记录本次运行习惯。
- `MiniMemory.build_prompt_instruction()`：把记忆生成给模型看的提示词。

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

`name` 可选。不传时会自动生成 `subagent01`、`subagent02` 这样的稳定名称。默认规则很小：带 `triggers` 时只在命中后运行；不带 `triggers` 时每次主 agent 运行都会调用该从 agent。主 agent 会把从 agent 输出作为 `Subagent results` 加入最终回答上下文。

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
name = "echo"
kind = "prompt"
description = "Minimal example skill"
version = "0.1.0"
agent_created = false
agent_can_update = false
freshness = 70
function_group = "general"
triggers = ["echo", "brief"]

[entry]
instructions = "SKILL.md"
```

`SKILL.md` 存放真正给 agent 的说明。runtime 会根据配置和触发词选择 skill，并把命中的说明注入本次运行。

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

### Skill 自更新

每个普通 skill 都可以标记两个字段：

- `agent_created`：是否由 agent 自己创建。
- `agent_can_update`：是否允许 agent 更新；不写时默认等于 `agent_created`。

也就是说，人类写的旧 skill 默认不可更新；agent 自己创建的 skill 默认可以继续更新和优化。

```python
agent = Agent.load_from_config_file("agent.toml")
agent.create_skill(
    "research-note",
    instructions="Summarize research notes with sources.",
    description="Research note helper",
    triggers=["research", "source"],
)
agent.update_skill("research-note", instructions="Write compact notes with sources.")
agent.optimize_skill("research-note", goal="make the instruction easier to follow")
```

CLI 也提供同一组轻量入口：

```bash
super-agent skills create --config agent.toml --name research-note --instructions "Summarize research notes."
super-agent skills update --config agent.toml --name research-note --instructions "Summarize with sources."
super-agent skills optimize --config agent.toml --name research-note --goal "make it clearer"
```

## MCP Skill 格式

MCP 也是一种 skill，放在统一 `skills/` 目录下：

```text
skills/mcp/filesystem/
  skill.toml
  SKILL.md
```

`skill.toml`：

```toml
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

runtime 会按 `kind = "mcp"` 把它加载成 MCP skill。环境变量只会把变量名写入提示词，不会把值注入模型上下文。

## 动态渐进式披露

每次运行都会建立一个披露缓存，默认位置：

```text
.super-agent/memory/disclosure/
  index.json
  history.jsonl
  skills/<skill-name>/manifest.json
  skills/<skill-name>/instructions.md
```

披露流程：

1. runtime 先写入 `index.json`，只暴露 skill 名称、描述、触发词和缓存路径。
2. 命中的 skill 才会披露 `instructions.md`，避免全量加载上下文。
3. 每次披露都会追加到 `history.jsonl`，保留历史披露路径。
4. 模型或上层 workflow 可以直接选择缓存路径，再通过 `ProgressiveDisclosure.read_cache(path)` 读取结果。

这给后续 ReAct、Plan、Loop 留出了工具接口：模型先看索引，再选择需要展开的 skill，而不是一开始读完所有资源。

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

## 记忆自更新

记忆也是一种 skill：

```text
skills/memory/default/
  skill.toml
```

```toml
name = "default"
kind = "memory"
description = "Default memory behavior"

[memory]
```

`MiniMemory` 现在会自动更新调用方式与习惯：

```text
.super-agent/memory/
  memory.md      # 人类或工具写入的长期记忆
  habits.json    # runtime 自动更新的调用习惯
```

每次成功运行后，runtime 会记录：

- 总运行次数。
- 使用过的 workflow。
- 命中的 skill。

下一次运行时，这些习惯会以 `Usage habits` 指令加入系统上下文。CLI 可以查看当前习惯：

```bash
super-agent memory habits --config agent.toml
```

## Workflow Skill 格式

workflow 也是一种 skill：

```text
skills/workflow/direct/
  skill.toml
```

```toml
name = "direct"
kind = "workflow"
description = "Direct workflow"

[workflow]
mode = "direct"
```

内置 `mode` 当前支持 `direct`、`plan`、`react`、`loop`。也可以通过 `instruction` 给 workflow 增加额外提示：

```toml
[workflow]
mode = "plan"
instruction = "Prefer short numbered steps."
```

## 架构

```text
src/
  cli.py         # CLI 入口：init、run、skills、memory
  core/          # Agent、子 agent 编排、agent.toml、provider
  skill/         # manifest、loader、kind、渐进式披露缓存、自更新、保鲜度
    kinds/       # prompt、mcp、memory、workflow 等 skill kind
  frontend/mac/  # SwiftUI 桌面端：对话、配置、运行树
```

首版刻意保持小内核。`plan`、`react`、`loop` 现在是轻量 workflow skill，后续可以升级成真正的工具循环和 goal loop，但不把复杂度提前塞进内核。

`memory` 当前采用最小 Markdown + JSON 存储，实现在 `skill/kinds/memory.py`。存在内容时，runtime 会把最近记忆和调用习惯加入本次运行。

macOS 前端会把会话保存成 JSON，并为每次运行生成类似文件树的运行节点。点击运行树里的主 agent 或子 agent 节点，可以查看该节点的输入与输出。

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
