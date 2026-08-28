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
SUPER_AGENT_PROVIDER=mock super-agent check
SUPER_AGENT_PROVIDER=mock super-agent "你好"
```

远程模型必须显式提供通用设置。`SUPER_AGENT_API_KEY_ENV` 保存变量名，密钥值只存在于该变量中：

Remote models require explicit generic settings. `SUPER_AGENT_API_KEY_ENV` stores the variable name, while the key value exists only in that variable:

```bash
export SUPER_AGENT_MODEL="your-model"
export SUPER_AGENT_BASE_URL="https://provider.example/v1"
export SUPER_AGENT_API_KEY_ENV="MODEL_API_KEY"
super-agent check
super-agent "解释这个仓库"
```

请先在外部 Shell 或密钥管理器中提供 `MODEL_API_KEY`；文档和配置文件只保存变量名，不保存密钥值。

Provide `MODEL_API_KEY` in your external shell or secret manager first; documentation and configuration files store only the variable name, never the key value.

`check` 只读取配置、插件与 Skill，不创建存储、不调用模型；没有模型时返回失败是预期行为。

`check` only reads configuration, plugins, and Skills. It does not create storage or call a model; failure without a model is intentional.

## 显式选择插件 / Select a Plugin Explicitly

内置 common 插件适合通用任务，code 插件适合仓库工作。模型也可以从索引自行选择相关 Skill；命令行参数只在需要固定本次方法时使用。

The builtin common plugin suits general tasks, while the code plugin suits repository work. The model may also select relevant Skills from the index; use a command-line option only to fix the method for this run.

```bash
super-agent plugins list
super-agent --plugin plugin:super-agent/code "检查当前仓库"
super-agent --skill skill:super-agent/review/multi-agent "检视这个方案"
```

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
