# Super Agent

[双语首页](README.md) | [English guide](README_en.md)

**一个简单、轻量、高智能、自进化、Skill 优先的 Agent 运行时。**

> Skill is all you need.

Super Agent 只给模型精简的插件与 Skill 索引。模型自行判断需要什么，按需打开内容，再激活选中的
方法。提示、运行策略、记忆方法、工具使用方法和任务指令使用同一种 Skill 格式，也经过同一条
渐进式披露路径；模型连接和密钥继续由显式配置管理。

默认 Python 安装没有第三方运行依赖。基础 `Agent()` 无状态且不写文件。存储、对话、记忆、
Skill 更新和 MCP 都是可选层；缺少必要条件时会明确失败。

Super Agent 仍是实验性的 `1.0` 前软件。破坏性修改不保留兼容别名和迁移薄壳。

## 一分钟开始

需要 Python 3.11 或更高版本。

```bash
python3.11 -m pip install -e .
SUPER_AGENT_PROVIDER=mock super-agent check
SUPER_AGENT_PROVIDER=mock super-agent "解释这个仓库"
```

`check` 只读取配置、Skill 和模型设置，不创建存储，也不调用模型。远程模型使用通用环境变量
或 `common.toml` 显式配置；项目不根据私人变量名猜测供应商、模型或地址：

```bash
export SUPER_AGENT_MODEL="your-model"
export SUPER_AGENT_BASE_URL="https://provider.example/v1"
export SUPER_AGENT_API_KEY_ENV="MODEL_API_KEY"
super-agent check
```

请先在外部 Shell 或密钥管理器中提供 `MODEL_API_KEY`；文档和配置文件只保存变量名，不保存密钥值。

不带参数运行 `super-agent` 会进入交互对话。程序不会生成项目文件；只有确实需要时才添加
`common.toml`、`cli.toml`、`code.toml` 或本地 Skill。

在对话中使用 `/help`、`/clear` 或 `/exit` 控制终端会话。

## 添加 Skill 和插件

Skill 与 MCP 由中央资源库统一管理，插件只保存引用，不拥有或复制内容。创建
`library/skills/local/research/SKILL.md`：

```markdown
---
name: research
description: 研究问题并整理证据；需要调查或形成带依据结论时使用。
---

先确认问题和证据范围，再给出带来源的结论。
```

需要组合多个 Skill 时，再创建 `library/plugins/local/research/plugin.toml`：

```toml
schema = 1
id = "local/research"
version = "0.1.0"
description = "研究方法集合"
entry_skill = "skill:local/research"
skills = []
included_plugins = []
required_mcp_servers = []
optional_mcp_servers = []
```

将 `library` 加入 `library_paths` 后，可直接启用 `skill:local/research`，也可用
`plugin:local/research` 激活插件引用的全部内容。多个插件引用同一 Skill 或 MCP 时只保留一个逻辑
实例；同 ID 的版本或内容不同则直接报冲突。系统没有触发词表，模型根据描述自行选择。进化权限
只在外部配置或代码中授予，Skill 和插件不能自授权。完整格式见 [Skill 文档](docs/skills.md)。

## Python 用法

常用模块只导出一个类：

```python
from super_agent import Agent, model_from_environment

agent = Agent(model_from_environment())
result = agent.run("解释 Skill 渐进式披露")
print(result.text)
```

`Agent` 常用的直白操作是 `run`、`for_user`、`add_group`、`add_subagent`、
`add_library_path`、`enable_plugin`、`enable_skill`、`add_tool` 和 `add_model`。高级类型从其所属模块导入。

专用 Agent 在代码中组合。任务 Skill 只属于本次运行，不会偷偷改变后续运行：

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

第 1 层始终是根组。普通组只组织 Agent，不调用模型；Agent 可以带着已有子树挂入任意组。
同级 Agent 通过父组共享板交换稳定引用。任务、等待唤醒、价格路由、断路重试、动态压缩和多模型
决策都由同一个树运行器管理，并按用户隔离。不添加组或子 Agent 时不会创建树运行状态。

需要旁观者共同检视时，启用 `skill:super-agent/common/review`。它先让至少两个不同 Agent 独立检查，
再交叉验证发现；多样性不足会明确失败，不会退化成执行者自检。

## 按需添加状态

```python
from adapter.storage import JsonlStorage
from super_agent import Agent, model_from_environment

agent = Agent(model_from_environment())
agent.use_storage(JsonlStorage(".super-agent/data"))
agent.enable_memory()
alice = agent.for_user("alice")
conversation = alice.conversations.create("项目")
alice.memory.remember_long_term("用户偏好简洁回答", labels=("preference",))
result = alice.run("继续分析项目", conversation_id=conversation.conversation_id)
print(alice.runs.explain(result.run_id))
```

对话消息是短期上下文。长期记忆只保存持久事实、偏好和抽象信息，并可显式整理或遗忘。
用户与 Agent 范围会隔离对话、记忆、运行记录、Skill 覆盖层和披露缓存。

JSONL 是可直接阅读的默认存储。SQLite 同样只用标准库；MySQL 和 PostgreSQL 驱动为可选依赖。

模型可以在 `common.toml` 的 `[[models]]` 中填写 `description`，作为用户给出的初始选择依据。这段说明不会被学习覆盖；`alice.models.list(purpose="code")` 会同时返回初始说明和按用户、Agent、任务类型隔离的表现画像。调用成功只更新可靠性、token、成本和延迟，不会冒充质量判断；`alice.models.evaluate_run(result.run_id, score=0.9)` 才会显式更新质量。

审计记录有保留期限且可以配置。详细记录默认保留 180 天，关键记录默认保留 365 天。原始事件完整
保存，供学习和复盘使用；`alice.runs.explain(run_id)` 默认动态将 prompt、模型输出、工具参数/结果
和错误消息替换为哈希与大小摘要，只有代码中显式传入 `include_sensitive=True` 才会读取原文。
动态脱敏不是存储加密，因此仍需保护存储后端；记录清理默认只预览，必须使用 `--apply` 才会删除到期记录。

检查点不会自动启用。可以把 `MemoryCheckpointStore` 或 `EventCheckpointStore` 放入运行上下文，先通过
`checkpoint_store.read(run_id)` 读取，再用
`AgentContext(checkpoint_store=..., resume_checkpoint=...)` 显式恢复。

## 显式更新 Skill

学习只记录评价、保鲜度和模型使用证据，不会修改 Skill。`SkillEvolution` 将更新拆成
`propose`、`test`、`apply` 和 `undo` 四个显式动作。提案和测试都不能启用候选内容；只有
`apply` 会修改用户 Skill 覆盖层，测试失败时禁止应用。可进化插件和 Skill 通过 `[evolution]`
的 `allow`、`auto_apply` 或同名代码 API 逐项授权，完整示例见[进化文档](docs/evolution.md)。

## CLI

```bash
super-agent check
super-agent "执行一次任务"
super-agent --plugin plugin:super-agent/code "检查这个仓库"
super-agent config show
super-agent plugins list
super-agent skills list
super-agent data storage verify --config common.toml
super-agent data storage prune --config common.toml --user alice
super-agent data storage prune --config common.toml --user alice --apply
super-agent data conversations list --config common.toml --user alice
```

单次运行默认无状态，只有显式传入 `--save` 才会保存。文本运行会在答案后显示结束原因和运行 ID；
集成程序可使用 `--output json` 读取完整运行结果。

可选的 `cli.toml` 只管理终端默认行为。共享 Runtime 设置放在 `common.toml`，编码工作区
设置放在 `code.toml`，模型连接放在 `common.toml` 或环境变量中。这些文件分别校验，绝不进行
深度合并。

代码任务提供有上限的目录树、UTF-8 文件范围读取、文本搜索，以及固定参数的 Git 状态和差异
读取。文件替换、结构化精确补丁和删除必须携带上次读取返回的 SHA-256，因此并发变化会明确失败，
不会被覆盖。忽略路径、越界路径、超大文件和非文本读取都会明确失败。`code.toml` 中的动作
可设为 `deny`、`ask` 或 `allow`；只有 `ask` 会在终端逐次确认，`deny` 不挂载工具，`allow`
按用户的显式配置直接执行。验证命令必须预先声明为参数
数组，启动后通过进程 ID 轮询或停止，并受到明确的时间与输出上限约束；系统不接受 shell 字符串。
`repository_map` 提供有上限的路径、哈希和符号地图。Python 符号使用标准 AST 解析；其他文件类型
不会猜测符号。`run_check` 会等待一个预先声明的检查并返回真实退出码。失败检查只是下一次模型
显式修改的依据；运行时不会自动修改文件，也不会隐藏失败的验证。

## 保证

- Skill、文件、工具输出、记忆上下文和子 Agent 结果都使用同一条中心化渐进式披露路径。
  大内容通过引用和有边界的分页读取，不会被静默截断。
- Skill 指令、工具结果、记忆上下文、子 Agent 结果和引用读取共享每轮一个上下文预算。预算用完后，
  模型只会收到稳定引用和哈希，必须显式请求下一页。
- 路由由模型判断，不使用关键词匹配。
- 读取不会修改业务状态；显式配置的披露缓存只写入有界、可丢弃的缓存文件。
- Skill 内容是被动数据，不能注册代码、权限或密钥。
- Provider、存储和可选功能失败会直接呈现，不进行隐藏退化。
- 基础运行不依赖存储、记忆、对话、安全规则或学习。

## 评测成绩

所有 Agent 均使用 `THUDM/GLM-4-9B-0414`，并采用各自的默认配置。成绩格式为
通过题数 / 总题数（通过率）。

| Agent | 模型 | HumanEval+ | LiveCodeBench Codegen |
| --- | --- | ---: | ---: |
| Codex | `THUDM/GLM-4-9B-0414` | 96 / 164（58.54%） | 166 / 612（27.12%） |
| Claude Code | `THUDM/GLM-4-9B-0414` | 100 / 164（60.98%） | 151 / 612（24.67%） |
| Super Agent | `THUDM/GLM-4-9B-0414` | 103 / 164（62.80%） | 156 / 612（25.49%） |

完整任务级报告、隔离运行器和本地评测资产说明位于 [`tests/eval/`](tests/eval/README.md)。

## 继续阅读

- [快速开始](docs/getting-started.md)
- [源码阅读路径](docs/source-tour.md)
- [Skill](docs/skills.md)
- [配置](docs/configuration.md)
- [运行时](docs/runtime.md)
- [CLI](docs/cli.md)
- [学习、记忆与 Skill 更新](docs/evolution.md)
- [安全](docs/safety.md)
- [致谢与借鉴](docs/acknowledgements.md)

可直接运行的示例位于 `examples/minimal.py`、`examples/custom_skill.py` 和
`examples/team.py`。

## 验证仓库

```bash
python3.11 scripts/verify_release.py --version 0.2.19 --full
```

完整的本地发布检查（包括版本一致性和打包范围）见[本地发布流程](docs/releasing.md)。

零依赖对照运行器和可复现约束见[评测说明](docs/benchmarks.md)。

## 致谢

项目、模块、许可证和借鉴边界见[致谢与借鉴详细文档](docs/acknowledgements.md)。
