# 快速开始 / Getting Started

## 环境 / Environment

项目支持 Python 3.11 及更高版本，不绑定操作系统或 Python 发行方式。

The project supports Python 3.11 and newer without requiring a specific operating system or Python distribution.

```bash
python3.11 -m pip install -e .
```

默认安装没有第三方 Python 运行依赖。默认 `Agent()` 不创建目录、不写文件，也不会在缺少模型时偷偷改用另一个模型。

The default install has no third-party Python runtime dependency. `Agent()` creates no directory or file and never silently substitutes a missing model.

## 第一次运行 / First Run

配置一个 OpenAI 兼容模型，或者显式使用离线 Mock：

Configure an OpenAI-compatible model, or explicitly select the offline Mock model:

```bash
export OA3_SILICONFLOW_API_KEY="your-key"
super-agent check
super-agent "解释这个仓库"

SUPER_AGENT_PROVIDER=mock super-agent check
SUPER_AGENT_PROVIDER=mock super-agent "你好"
```

设置 `OA3_SILICONFLOW_API_KEY` 时，运行时会使用 `THUDM/GLM-4-9B-0414` 和 `https://api.siliconflow.cn/v1`。

When `OA3_SILICONFLOW_API_KEY` is set, the runtime selects `THUDM/GLM-4-9B-0414` at `https://api.siliconflow.cn/v1`.

`check` 只读取配置和 Skill，不创建存储、不调用模型；没有模型时返回失败是预期行为。

`check` only reads configuration and Skills. It does not create storage or call a model; failure without a model is intentional.

## Python 嵌入 / Embed in Python

```python
from core.provider import MockModel
from super_agent import Agent

agent = Agent(MockModel("offline response"))
result = agent.run("Say hello")
print(result.text)
```

`run` 和 `stream` 共享同一条运行循环；流式调用可以监听 `RunEvent`，同步调用只是收集同一条事件流。

`run` and `stream` share one execution loop; streaming exposes `RunEvent` values, while the synchronous method only collects that same stream.

## 显式添加状态 / Add State Explicitly

```python
from adapter.storage import JsonlStorage
from super_agent import Agent, model_from_environment

agent = Agent(model_from_environment())
agent.use_storage(JsonlStorage(".super-agent/data"))
user = agent.for_user("alice")
conversation = user.conversations.create("项目")
result = user.run("记录本轮任务", conversation_id=conversation.conversation_id)
print(result.run_id)
```

JSONL 是默认推荐后端；SQLite 使用标准库，MySQL 和 PostgreSQL 需要对应 optional extra。`use_storage` 是显式动作，读取配置本身不会创建后端。

JSONL is the recommended default backend; SQLite uses the standard library, while MySQL and PostgreSQL require their optional extras. `use_storage` is explicit, and loading configuration does not create a backend.

## 下一步 / Next

- [架构 / Architecture](architecture.md)
- [Skill](skills.md)
- [配置 / Configuration](configuration.md)
- [CLI](cli.md)
- [运行时 / Runtime](runtime.md)
