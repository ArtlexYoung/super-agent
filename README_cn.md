# Super Agent

[English README](README.md)

**一个简单、轻量、自进化、Skill 优先的 Agent 运行时。**

> Skill is all you need.

Super Agent 只给模型一份精简的 Skill 索引。模型按需打开 Skill，使用其中的工具和指令，
最后返回结果。提示、工具、记忆、工作流、任务场景和模型说明都使用同一种 Skill 格式，
也都经过同一个渐进式披露核心。

默认 Python 运行时没有第三方依赖。存储、对话、记忆、安全规则、MCP、学习和进化均可选。
启用的功能缺少必要条件时会明确失败，不会偷偷切换实现或降低能力。

Super Agent 仍处于实验性的 `0.0.x` 阶段；破坏性修改不会保留旧导入和兼容薄壳。

## 开始使用

需要 Python 3.11 或更高版本。

```bash
python3 -m pip install -e .
export OPENAI_API_KEY="..."
super-agent "解释这个仓库"
```

不带参数运行 `super-agent` 即可进入交互对话。OpenAI 兼容接口、Anthropic 兼容接口和
Ollama 的环境配置会被自动发现。离线测试必须明确选择 Mock Provider：

```bash
SUPER_AGENT_PROVIDER=mock super-agent "你好"
```

默认不需要 `agent.toml`。只有希望获得可编辑项目和示例 Skill 时，才运行
`super-agent init --path my-agent`。

## Python 用法

常用 API 只有一个类：

```python
from super_agent import Agent

agent = Agent()
result = agent.run("解释 Skill 的渐进式披露")
print(result.text)
```

`Agent()` 默认不启用存储，也不会创建文件。高级集成从类型所属模块导入，依赖边界更加直观：

```python
from core.provider.chat import MockProvider
from super_agent import Agent

agent = Agent(provider=MockProvider("离线结果"))
print(agent.run("对文本进行分类").text)
```

## Skill

一个 Skill 从精简的 `skill.toml` 开始。只有模型选择它以后，指令和资源才会被打开。

```toml
schema_version = 3
name = "research"
type = "prompt"
description = "研究一个问题并给出带引用的结论"
version = "0.1.0"
agent_created = false
agent_can_update = false

[entry]
instructions = "SKILL.md"
```

系统没有预设触发词。模型在正常调用中根据描述选择 Skill 和任务场景。场景只是具名的
Skill 组合，因此不同 Agent 可以各有专长，不需要另一套执行系统：

```python
from super_agent import Agent

main = Agent()
coder = Agent()
coder.use_only_scenes("code")
main.add_subagent(coder, name="coder", description="实现并验证代码修改")
```

模型说明本身也是 Skill。一个模型 Skill 标记为默认模型；其他已就绪模型会通过
`use_model(model, prompt, reason)` 提供给默认模型。默认模型可以根据各模型声明的支持项和
特长显式分配子任务。被委派的调用不会替换主循环，也不会在失败后切换到其他 Provider。

## 可选状态

CLI 和 Web 会明确使用配置的存储；嵌入式 Python 需要主动启用：

```python
agent = Agent(use_storage=True)
alice = agent.for_user("alice")
result = alice.run("记住我偏好简洁回答")
learning = alice.runs.learn(result.run_id)
```

对话消息就是本轮短期上下文。长期记忆只保存持久、关键的信息，并允许模型回忆、整理和遗忘。
不同用户和 Agent 的对话、记忆、运行证据及 Skill 覆盖层彼此隔离。

默认存储是本地 JSONL。SQLite 同样只用标准库；MySQL 和 PostgreSQL 驱动是可选依赖。

## CLI 与 Web

CLI 只有五个顶层命令组：

```bash
super-agent init --path my-agent
super-agent run "执行一次任务"
super-agent run --chat --user-id alice
super-agent skills list
super-agent data runs status
super-agent serve
```

`skills models` 管理模型 Skill，`skills evolution` 查看 Skill 迭代。`data` 统一包含对话、
长期记忆、运行记录和存储复制。React 页面、CopilotKit 示例和 AG-UI 接口默认位于
`http://127.0.0.1:8765/`。

## 保证

- 所有 Skill 类型共享一个渐进式披露路径。
- 不使用关键词匹配，不隐藏切换 Provider、Mock 或存储实现。
- 读取不会写入；状态修改必须经过显式、受检查的动作。
- Skill 内容默认是被动数据，只有应用代码注册后才能执行工具。
- Skill 候选修改必须先评估，再明确提升，并可回滚。
- 基础运行不依赖存储、记忆、对话、安全或进化。

## 文档

- [快速开始](docs/getting-started.md)
- [架构](docs/architecture.md)
- [源码阅读路径](docs/source-tour.md)
- [Skill](docs/skills.md)
- [配置](docs/configuration.md)
- [运行时](docs/runtime.md)
- [CLI](docs/cli.md)
- [记忆与进化](docs/evolution.md)
- [安全](docs/safety.md)
- [Web](docs/web.md) 与 [AG-UI](docs/ag-ui.md)

## 开发验证

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests python3 -m unittest discover -s tests -p 'test_*.py'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m compileall -q src
pnpm --dir web typecheck
pnpm --dir web lint
pnpm --dir web build
```
