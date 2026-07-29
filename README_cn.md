# Super Agent

[English README](README.md)

**一个简单、轻量、自进化、Skill 优先的 Agent 运行时。**

> Skill is all you need.

提示、工具、记忆、工作流、规划器和模型说明都使用同一种 Skill 格式。统一的渐进式披露
核心负责找到相关内容，一套 Runtime 负责规划和执行任务。存储、记忆、学习与进化都是可选
层，启用后仍走同一条链路，不会各自建立一套引擎。

Super Agent 仍处于实验性的 `0.0.x` 阶段。破坏性修改会直接完成，不保留旧导入或隐式兼容
行为。

## 60 秒开始使用

需要 Python 3.11 或更高版本。默认 Python Runtime 没有第三方依赖。

```bash
python3 -m pip install -e .
export OPENAI_API_KEY="..."
super-agent "解释这个仓库"
```

这就是默认使用所需的全部设置。也会自动发现 `ANTHROPIC_API_KEY` 和 `OLLAMA_HOST`，无需
初始化项目或创建 `agent.toml`。离线冒烟检查需要明确选择 Mock Provider：

```bash
SUPER_AGENT_PROVIDER=mock super-agent "你好"
```

没有可用模型时，运行会返回配置错误。Provider 调用失败会原样抛出，Runtime 不会偷偷切换
模型或替换成 Mock。

不带提示词运行 `super-agent` 可以进入终端对话。

## 作为 Python 库使用

```python
from super_agent import Agent

agent = Agent()
result = agent.run("解释渐进式 Skill 披露")

print(result.text)
print(result.run_id)
```

`Agent()` 使用惰性初始化，首次使用时才发现模型和相关 Skill。只有应用明确要求时才会打开
存储。

Python 库默认不启用存储，适合不需要会话、记忆、缓存或持久化证据的嵌入式任务：

```python
from super_agent import Agent, MockProvider

agent = Agent(provider=MockProvider("离线结果"))
result = agent.run("对这段文本分类")
print(result.events)
```

这个模式下请求依赖存储的功能会直接报错。Runtime 不会为了继续运行而删除功能、重试其他
模型或替换成 Mock。

## 在代码中添加专业能力

场景是可选的任务领域 Skill 集合。Runtime 可以自动选择，每个 Agent 也可以独立限定范围：

```python
from super_agent import Agent

main = Agent()
coder = Agent()
coder.use_only_scenes("code")

main.add_subagent(coder, name="coder", triggers=["代码", "实现"])
result = main.run("实现并测试这个改动")
```

使用 `disable_scenes()` 可直接调用模型。只有需要可编辑 Skill 示例时才运行
`super-agent init --path my-agent`。提示、工具、记忆、工作流、规划器、场景和模型说明都使用
同一种渐进披露 Skill 格式。

## 按需启用状态与学习

CLI 与 Web 使用 `.super-agent/` 下可直接阅读的本地 JSONL。Python 库通过
`Agent(use_storage=True)` 启用。SQLite 无第三方依赖；MySQL 与 PostgreSQL 驱动只在明确
选择后加载。所有有状态记录都按用户和 Agent 隔离。

```python
agent = Agent(use_storage=True)
alice = agent.for_user("alice")
result = alice.run("记住我偏好的回答风格")
learning = alice.runs.learn(result.run_id)
```

`learn_from_run` 是评价与进化的显式边界。允许更新的 Agent 自建 Skill 会分别经过候选、
不退化评价、晋升、监控和回滚阶段。准备或评价候选不会激活它。

## Web 与运行检查

```bash
super-agent serve
super-agent run --scene code "检查这个改动"
super-agent runs explain --run-id <run-id>
super-agent skills index --output json
```

React 客户端、CopilotKit 示例与 AG-UI 接口位于 `http://127.0.0.1:8765/`。

## 设计保证

- **渐进式：** 只加载相关 Skill 内容。
- **显式：** 读取不写入，缺少条件时在模型执行前失败。
- **隔离：** 用户和 Agent 不共享会话、记忆、证据或覆盖层。
- **可进化：** 每项变更都经过评价、记录、显式晋升并可回滚。
- **默认被动：** Skill 内容不能给自己授予代码执行或操作权限。

## 文档

- [快速开始](docs/getting-started.md)
- [架构](docs/architecture.md)
- [Skill 与渐进式披露](docs/skills.md)
- [Skill loader 与 MCP](docs/skill-loaders.md)
- [配置与存储](docs/configuration.md)
- [Runtime、追踪、用户与子 Agent](docs/runtime.md)
- [记忆、评价、保鲜度与进化](docs/evolution.md)
- [动作规则](docs/safety.md)
- [CLI 参考](docs/cli.md)
- [Web 客户端](docs/web.md)
- [AG-UI](docs/ag-ui.md)
- [路线图](docs/roadmap.md)

## 开发

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q src tests
pnpm --dir web lint
pnpm --dir web typecheck
pnpm --dir web build
```

发布测试会约束源码布局、导入边界、函数与文件大小、控制流和目录子项数量。`0.0.x` 阶段
不提供内部导入兼容层。
