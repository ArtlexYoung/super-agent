# Super Agent

[English README](README.md)

**一个简单、轻量、自进化、Skill 优先的 Agent 运行时。**

> Skill is all you need.

提示、工具、记忆、工作流、规划器和模型说明都使用同一种 Skill 格式。统一的渐进式披露
核心负责找到相关内容，一套 Runtime 负责规划和执行任务。存储、记忆、学习与进化都是可选
层，启用后仍走同一条链路，不会各自建立一套引擎。

Super Agent 仍处于实验性的 `0.0.x` 阶段。破坏性修改会直接完成，不保留旧导入或隐式兼容
行为。

## 三条命令开始使用

需要 Python 3.11 或更高版本。默认 Python Runtime 没有第三方依赖。

```bash
python3 -m pip install -e .
export OPENAI_API_KEY="..."
super-agent "解释这个仓库"
```

也会自动发现 `ANTHROPIC_API_KEY` 和 `OLLAMA_HOST`。无需初始化项目，也无需创建
`agent.toml`。确定性离线检查需要明确选择 Mock Provider：

```bash
SUPER_AGENT_PROVIDER=mock super-agent "你好"
```

没有可用模型时，运行会返回配置错误。Provider 调用失败会原样抛出，Runtime 不会偷偷切换
模型或替换成 Mock。

不带提示词运行 `super-agent` 可以进入终端对话。运行 `super-agent serve` 可在
`http://127.0.0.1:8765/` 打开 React 客户端和 AG-UI 接口。

## 作为 Python 库使用

```python
from super_agent import Agent

agent = Agent()
result = agent.run("解释渐进式 Skill 披露")

print(result.text)
print(result.run_id)
```

`Agent()` 使用惰性初始化。配置会立即校验；Skill 扫描、模型发现和 Runtime 组装会等到
首次使用时才发生。只有应用明确启用或传入存储时才会打开存储。因此可以先注册 Skill
loader、MCP 服务、事件订阅者和子 Agent。

Python 库默认不启用存储，适合不需要会话、记忆、缓存或持久化证据的嵌入式任务：

```python
from super_agent import Agent, MockProvider

agent = Agent(provider=MockProvider("离线结果"))
result = agent.run("对这段文本分类")
print(result.events)
```

这个模式下请求依赖存储的功能会直接报错。Runtime 不会为了继续运行而删掉用户请求的功能。

## 一套运行模型

```text
Provider     连接模型智能
Core         规划运行、执行任务并管理可选状态
SkillLoader  将一种 Skill 转为运行行为
Skill        保存被动内容与配置
Agent        在 Python 中组合 Provider、SkillLoader、可选存储和子 Agent
```

每次任务只走一条链路：

```text
Agent.run
  -> 创建一个完整 Run
  -> 可选地选择一个任务场景，渐进披露所需 Skill
  -> 使用一个 Scheduler Skill 创建只含一次模型决定的 Plan
  -> 预检 SkillLoader、服务、工具、Provider 和子 Agent
  -> 执行并产生一条有序事件流
  -> 可选地从不可变证据中学习
```

这套设计有四个直接保证：

- **渐进式：** 只读 Skill 发现不需要存储；只有选中的有状态功能才会加入运行。
- **显式：** 读取不会写入。变更有可见的准备与应用阶段。缺少服务或模型输出无效时直接
  失败，不会偷偷缩减功能。
- **隔离：** 会话、记忆、证据、披露历史和用户 Skill 覆盖层都按用户与 Agent 隔离。
- **可进化：** 显式调用 `learn_from_run` 后才评价证据。允许更新的 Agent 自建 Skill 可以
  依次经过候选、不退化评价、晋升、监控和回滚。

Skill 内容只是被动数据，不能给自己授予执行权限。可执行 MCP 服务和自定义 Skill loader
必须在可信应用代码中注册，工具也必须在预检前声明操作效果。

## Skill 与场景

随软件提供的场景是 `skill` 包内的普通被动 Skill：

```text
src/skill/builtin/
  scene/common/   通用任务 Skill 组合
  scene/code/     仓库任务 Skill 组合
```

Runtime 可以根据本次明确指定或提示词触发器选择场景。每个 Agent 都可以独立使用全部场景、
调用 `use_only_scenes(...)` 限定场景，或者调用 `disable_scenes()` 禁用场景。没有兼容场景
时，Plan 会记录 `scene=null` 并使用 Runtime 的直接模型机制；这是可见模式，不是 fallback。
专业化只在 Python 代码中组合，不写入 TOML。出现歧义时直接报错。应用可以通过
`Agent.add_skill_loader(...)` 注册任意 Skill 类型。

只有需要可编辑示例时才运行 `super-agent init --path my-agent`。模型配置是 model Skill，
API 密钥仍放在环境变量中。格式详见 [Skill](docs/skills.md) 与
[配置](docs/configuration.md)。

## 可选层

CLI 与 Web 应用明确使用 `.super-agent/` 下可直接阅读的本地 JSONL。Python 库可通过
`Agent(use_storage=True)` 启用相同后端。SQLite 同样只使用标准库；MySQL 与 PostgreSQL
驱动只在明确选择对应后端后作为可选依赖加载。

常用入口：

```bash
super-agent run --scene code "检查这个改动"
super-agent skills index --output json
super-agent runs explain --run-id <run-id>
super-agent runs learn --run-id <run-id>
super-agent skills freshness
```

子 Agent 通过 Python 中的 `Agent.add_subagent(...)` 组合，不写入 TOML。记忆、工作流、
规划、MCP、场景和模型配置仍然都是普通 Skill 类型。

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
