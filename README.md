# Super Agent

Super Agent 是一个简单、轻量、配置化的 **skill-first agent runtime**。

目标不是再造一个重型编排框架，而是把 agent 能力拆成可渐进加载的 skill：prompt、工具、记忆、workflow 都可以用同一套格式声明、组合和复用。

## 核心定位

- **CLI 优先**：直接初始化、运行、管理 skill，适合真实使用和开源传播。
- **Python 库内核化**：CLI 只是一层入口，第三方可以把同一个 runtime 嵌进服务、脚本、Notebook、CI 或桌面应用。
- **渐进式 skill**：先看索引，再读 manifest，命中后才加载 `SKILL.md` 和资源，避免一次性塞满上下文。
- **轻依赖**：首版只使用 Python 标准库，方便审计、安装和二次开发。

## 快速开始

```bash
python -m super_agent.cli init --path demo-agent
python -m super_agent.cli run --config demo-agent/agent.toml "hello"
python -m super_agent.cli skills list --config demo-agent/agent.toml
python -m super_agent.cli memory habits --config demo-agent/agent.toml
```

安装为命令后：

```bash
super-agent init --path demo-agent
super-agent run --config demo-agent/agent.toml "hello"
super-agent skills list --config demo-agent/agent.toml
super-agent memory habits --config demo-agent/agent.toml
```

默认配置使用 `mock` provider，不需要 API key，方便本地验证。

## Python API

```python
from super_agent import Agent, AgentConfig

config = AgentConfig.from_file("agent.toml")
agent = Agent(config)
result = agent.run("总结这个项目的设计")

print(result.text)
```

## 配置格式

```toml
[agent]
name = "super-agent"
system = "You are a concise, helpful agent."
workflow = "direct"
skills = ["echo"]

[model]
provider = "mock"
model = "mock"

[paths]
skills = ["skills"]
memory = ".super-agent/memory"
```

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
description = "Minimal example skill"
version = "0.1.0"
triggers = ["echo", "brief"]

[entry]
instructions = "SKILL.md"
```

`SKILL.md` 存放真正给 agent 的说明。runtime 会根据配置和触发词选择 skill，并把命中的说明注入本次运行。

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

## 记忆自更新

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

## 架构

```text
src/super_agent/
  cli.py         # CLI 入口：init、run、skills list
  core/          # Agent、agent.toml、provider
  skill/         # skill manifest、loader、selector、渐进式披露缓存
  workflow/      # direct、plan、react、loop 的轻量 workflow
  memory/        # mini memory 与调用习惯自更新
```

首版刻意保持小内核。`plan`、`react`、`loop` 现在是轻量 prompt workflow，后续可以升级成真正的工具循环和 goal loop，但不把复杂度提前塞进内核。

`memory` 当前采用最小 Markdown + JSON 存储。存在内容时，runtime 会把最近记忆和调用习惯加入本次运行；后续可以把它升级为完整 skill 包。

## 开发验证

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## 设计原则

- 内核只负责装配和运行，不持有业务 prompt。
- skill 是扩展边界，不是写死在代码里的分支。
- memory、workflow、tool 都应该能逐步迁移成 skill。
- 默认失败要清楚，不用隐式 fallback 掩盖配置错误。
- 新能力先用最小 API 跑通，再沉淀成稳定 schema。
