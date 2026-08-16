# Super Agent

[双语首页](README.md) | [English guide](README_en.md)

**一个简单、轻量、高智能、自进化、Skill 优先的 Agent 运行时。**

> Skill is all you need.

Super Agent 只给模型一份精简的 Skill 索引。模型自行判断需要什么，按需打开内容，再执行选中的
指令或已注册工具。提示、运行策略、记忆方法、工具使用方法和任务指令使用同一种 Skill 格式，
也经过同一条渐进式披露路径；模型连接和密钥继续由显式配置管理。

默认 Python 安装没有第三方运行依赖。基础 `Agent()` 无状态且不写文件。存储、对话、记忆、
Skill 更新和 MCP 都是可选层；缺少必要条件时会明确失败。

Super Agent 仍是实验性的 `1.0` 前软件。破坏性修改不保留兼容别名和迁移薄壳。

## 一分钟开始

需要 Python 3.11 或更高版本。

```bash
python3.11 -m pip install -e .
export OA3_SILICONFLOW_API_KEY="..."
super-agent check
super-agent "解释这个仓库"
```

`check` 只读取配置、Skill 和模型设置，不创建存储，也不调用模型。`OA3_SILICONFLOW_API_KEY`
会选择文档中的免费模型；其他接口使用通用环境变量或 `common.toml`。离线冒烟测试必须显式启用 Mock：

```bash
SUPER_AGENT_PROVIDER=mock super-agent check
SUPER_AGENT_PROVIDER=mock super-agent "你好"
```

不带参数运行 `super-agent` 会进入交互对话。程序不会生成项目文件；只有确实需要时才添加
`common.toml`、`cli.toml`、`code.toml` 或本地 Skill。

在对话中使用 `/help`、`/clear` 或 `/exit` 控制终端会话。

## 添加 Skill

一个 Skill 只是带 TOML front matter 的 Markdown 文件：

```markdown
+++
name = "research"
type = "prompt"
description = "研究一个问题并给出带引用的结论"
version = "1.0.0"
created_by = "user"
agent_can_update = false
categories = ["research"]
+++

先确认问题和证据范围，再给出带来源的结论。
```

将文件放入 `skill_paths` 指向的任意目录即可。系统没有触发词表，模型在正常调用中根据描述
自行判断披露或启用哪些 Skill。`requires` 声明缺失即失败的必要工具，`optional_tools` 声明
已经注册就挂载、没有注册也不阻断方法本身的工具。

## Python 用法

常用模块只导出一个类：

```python
from super_agent import Agent, model_from_environment

agent = Agent(model_from_environment())
result = agent.run("解释 Skill 渐进式披露")
print(result.text)
```

`Agent` 最常用的六个直白操作是 `run`、`for_user`、`add_subagent`、`add_skill_path`、
`add_tool` 和 `add_model`。高级类型从其所属模块导入。

专用 Agent 在代码中组合。任务 Skill 只属于本次运行，不会偷偷改变后续运行：

```python
from super_agent import Agent, model_from_environment

main = Agent(model_from_environment())
coder = Agent(model_from_environment())
main.add_subagent(coder, name="coder", description="实现并验证代码修改")
result = coder.run("修复失败的测试", skill="code")
```

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
用户与 Agent 范围会隔离对话、记忆、运行记录和 Skill 覆盖层。

JSONL 是可直接阅读的默认存储。SQLite 同样只用标准库；MySQL 和 PostgreSQL 驱动为可选依赖。

审计记录有保留期限且可以配置。详细记录默认保留 180 天，关键记录默认保留 365 天。原始事件完整
保存，供学习和复盘使用；`alice.runs.explain(run_id)` 默认动态将 prompt、模型输出、工具参数/结果
和错误消息替换为哈希与大小摘要，只有代码中显式传入 `include_sensitive=True` 才会读取原文。
动态脱敏不是存储加密，因此仍需保护存储后端；记录清理由选中的后端在写入时按保留策略执行。

## 显式更新 Skill

学习只记录评价、保鲜度和模型使用证据，不会修改 Skill。`SkillEvolution` 将更新拆成
`propose`、`test`、`apply` 和 `undo` 四个显式动作。提案和测试都不能启用候选内容；只有
`apply` 会修改用户覆盖层，测试失败时禁止应用。完整示例见[进化文档](docs/evolution.md)。

## CLI

```bash
super-agent check
super-agent "执行一次任务"
super-agent --skill code "检查这个仓库"
super-agent config show
super-agent skills list
super-agent data storage verify --config common.toml
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

可直接运行的示例位于 `examples/minimal.py`、`examples/custom_skill.py` 和
`examples/team.py`。

## 验证仓库

```bash
python3.11 scripts/verify_release.py --version 0.2.0 --full
```

完整的本地发布检查（包括版本一致性和打包范围）见[本地发布流程](docs/releasing.md)。

零依赖对照运行器和可复现约束见[评测说明](docs/benchmarks.md)。

## 致谢

完整的项目、模块、许可证和借鉴边界说明见[双语致谢](README.md#致谢与借鉴)。
