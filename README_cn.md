# Super Agent

[双语首页](README.md) | [English guide](README_en.md)

**一个简单、轻量、高智能、自进化、Skill 优先的 Agent 运行时。**

> Skill is all you need.

Super Agent 只给模型一份精简的 Skill 索引。模型自行判断需要什么，按需打开内容，再执行选中的
指令或已注册工具。提示、运行策略、记忆行为、工具、任务指令和模型说明使用同一种 Skill 格式，
也经过同一条渐进式披露路径。

默认 Python 安装没有第三方运行依赖。基础 `Agent()` 无状态且不写文件。存储、对话、记忆、
Skill 更新、MCP 和 Web 都是可选层；缺少必要条件时会明确失败。

Super Agent 仍是实验性的 `1.0` 前软件。破坏性修改不保留兼容别名和迁移薄壳。

## 一分钟开始

需要 Python 3.11 或更高版本。

```bash
python3 -m pip install -e .
export OPENAI_API_KEY="..."
super-agent check
super-agent "解释这个仓库"
```

`check` 只读取配置、Skill 和模型设置，不创建存储，也不调用模型。系统可以从环境中发现
OpenAI 兼容接口、Anthropic 兼容接口和 Ollama。离线冒烟测试必须显式启用 Mock：

```bash
SUPER_AGENT_PROVIDER=mock super-agent check
SUPER_AGENT_PROVIDER=mock super-agent "你好"
```

不带参数运行 `super-agent` 会进入交互对话。程序不会生成项目文件；只有确实需要时才添加
`common.toml`、`cli.toml`、`code.toml` 或本地 Skill。

在对话中使用 `/help`、`/clear` 或 `/exit` 控制终端会话。

## 添加 Skill

一个 Skill 可以只有目录、精简清单和可选指令：

```text
skills/prompt/research/
  skill.toml
  SKILL.md
```

```toml
description = "研究一个问题并给出带引用的结论"
```

目录名就是 Skill 名称，`type` 默认为 `prompt`。系统没有触发词表，模型在正常调用中根据描述
自行判断披露或启用哪些 Skill。

## Python 用法

常用模块只导出一个类：

```python
from super_agent import Agent

agent = Agent()
result = agent.run("解释 Skill 渐进式披露")
print(result.text)
```

`Agent` 只有六个直白操作：`run`、`for_user`、`add_subagent`、`add_skill_path`、
`add_tool` 和 `add_model`。高级类型从其所属模块导入。

专用 Agent 在代码中组合。任务 Skill 只属于本次运行，不会偷偷改变后续运行：

```python
from super_agent import Agent

main = Agent()
coder = Agent()
main.add_subagent(coder, name="coder", description="实现并验证代码修改")
result = coder.run("修复失败的测试", skill="code")
```

## 按需添加状态

```python
agent = Agent(use_storage=True)
alice = agent.for_user("alice")
conversation = alice.conversations.create("项目")
result = alice.run("记住我的回答习惯", conversation_id=conversation.conversation_id)
alice.runs.learn(result.run_id)
```

对话消息是短期上下文。长期记忆只保存持久事实、偏好和抽象信息，并可显式整理或遗忘。
用户与 Agent 范围会隔离对话、记忆、运行记录和 Skill 覆盖层。

JSONL 是可直接阅读的默认存储。SQLite 同样只用标准库；MySQL 和 PostgreSQL 驱动为可选依赖。

审计记录有保留期限且可以配置。详细记录默认保留 180 天，关键记录默认保留 365 天。原始事件完整
保存，供学习和复盘使用；CLI 与 Web 的运行视图会动态将 prompt、模型输出、工具参数/结果和错误
消息替换为哈希与大小摘要。CLI 的运行状态、解释和导出命令只有显式添加
`--include-sensitive` 才会显示原文。动态脱敏不是存储加密，因此仍需保护存储后端。使用
`super-agent data storage prune` 预览清理内容，明确添加 `--apply` 才会删除。

## 显式更新 Skill

学习只记录评价、保鲜度和模型使用证据，不会修改 Skill。Skill 更新包含四个可见步骤：

```bash
super-agent manage skill-changes propose --name prompt:research --goal "让引用更清晰"
super-agent manage skill-changes test --change-id <id> --cases cases.json
super-agent manage skill-changes apply --change-id <id>
super-agent manage skill-changes undo --change-id <id>
```

提案和测试都不能启用候选内容。只有 `apply` 会修改用户覆盖层，测试失败时禁止应用。

## CLI 与 Web

```bash
super-agent check
super-agent "执行一次任务"
super-agent --skill code "检查这个仓库"
super-agent config show
super-agent skills list
super-agent data runs status
super-agent serve
```

单次运行默认无状态，只有显式传入 `--save` 或会话 ID 才会保存。文本运行会在答案后显示真实
使用的模型、任务 Skill、工作流、Skill、结束原因和运行 ID。集成程序可使用 `--output json`。
React 页面、CopilotKit 示例和 AG-UI 接口默认位于
`http://127.0.0.1:8765/`。

可选的 `cli.toml` 只管理终端默认行为。共享 Runtime 设置放在 `common.toml`，编码工作区
设置放在 `code.toml`，模型连接放在模型 Skill 或环境变量中。这些文件分别校验，绝不进行
深度合并。

代码任务提供有上限的目录树、UTF-8 文件范围读取、文本搜索，以及固定参数的 Git 状态和差异
读取。文件替换、结构化精确补丁和删除必须携带上次读取返回的 SHA-256，因此并发变化会明确失败，
不会被覆盖。忽略路径、越界路径、超大文件和非文本读取都会明确失败。所有非读取工具执行前都会
在终端请求确认；拒绝确认或输入结束会停止操作，不会偷偷降级。验证命令必须预先声明为参数
数组，启动后通过进程 ID 轮询或停止，并受到明确的时间与输出上限约束；系统不接受 shell 字符串。
`refresh_repository_map` 提供有上限的路径和元数据地图，并在本次运行中复用未变化文件的符号解析。
Python 符号使用标准 AST 解析；其他文件类型会明确标记没有解析器，不会猜测符号。
`run_declared_check` 会等待一个预先声明的检查并根据真实退出码返回 `passed`。失败检查只是下一次
模型显式修改的依据；运行时不会自动修改文件，也不会隐藏失败的验证。
长任务会创建不含原文的检查点，只记录步骤、选中的 Skill、工作流和哈希。使用
`user.runs.list_checkpoints(run_id)` 查看，使用 `user.runs.resume(run_id, prompt,
checkpoint_id=...)` 从指定检查点创建新的显式运行；由于不重建模型输出，继续意图由新的 prompt 提供。

## 保证

- Skill、文件、工具输出、记忆上下文和子 Agent 结果都使用同一条中心化渐进式披露路径。
  大内容通过引用和有边界的分页读取，不会被静默截断。
- Skill 指令、工具结果、记忆上下文、子 Agent 结果和引用读取共享每轮一个上下文预算。预算用完后，
  模型只会收到稳定引用和哈希，必须显式请求下一页。
- 路由由模型判断，不使用关键词匹配。
- 读取不会写入，状态修改必须经过显式、受检查的动作。
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

## 继续阅读

- [快速开始](docs/getting-started.md)
- [源码阅读路径](docs/source-tour.md)
- [Skill](docs/skills.md)
- [配置](docs/configuration.md)
- [运行时](docs/runtime.md)
- [CLI](docs/cli.md)
- [学习、记忆与 Skill 更新](docs/evolution.md)
- [安全](docs/safety.md)
- [Web](docs/web.md) 与 [AG-UI](docs/ag-ui.md)

可直接运行的示例位于 `examples/minimal.py`、`examples/custom_skill.py` 和
`examples/team.py`。

## 验证仓库

```bash
python3.11 scripts/verify_release.py --version 0.1.102 --full --web
```

完整的本地发布检查（包括版本一致性和打包范围）见[本地发布流程](docs/releasing.md)。

零依赖对照运行器和可复现约束见[评测说明](docs/benchmarks.md)。

## 致谢

完整的项目、模块、许可证和借鉴边界说明见[双语致谢](README.md#致谢与借鉴)。
