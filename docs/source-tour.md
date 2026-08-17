# 源码阅读路径 / Source Tour

建议从公开入口开始，而不是从适配器或内置 Skill 目录开始。

Start at the public entry point instead of beginning with adapters or builtin Skill files.

1. `src/super_agent.py`：查看 `Agent` 如何组合配置、模型、Skill、存储、组和子 Agent。
2. `src/core/run.py`：查看唯一的流式模型与工具循环。
3. `src/core/model.py`：查看中立消息、工具和模型事件。
4. `src/core/provider.py`：查看 Mock、OpenAI-compatible、Anthropic 和模型路由。
5. `src/core/disclosure.py`：查看所有大内容共用的分页、缓存路径和历史。
6. `src/skill/document.py` 与 `src/skill/library.py`：查看 Skill 格式、索引和原子激活。
7. `src/skill/organization.py` 与 `organization_runtime.py`：查看树结构、共享板和统一运行器；任务、工具和选择细节位于同名前缀文件。
8. `src/core/records.py` 与 `src/core/user.py`：查看记录契约、用户作用域、审计脱敏和保留策略。
9. `src/adapter/`：最后查看 CLI、存储、数据库、工作区和进程适配器。

1. `src/super_agent.py`: how `Agent` composes configuration, models, Skills, storage, groups, and subagents.
2. `src/core/run.py`: the single streaming model/tool loop.
3. `src/core/model.py`: neutral messages, tools, and model events.
4. `src/core/provider.py`: Mock, OpenAI-compatible, Anthropic, and model routing.
5. `src/core/disclosure.py`: shared paging, cache paths, and history for all large content.
6. `src/skill/document.py` and `src/skill/library.py`: the Skill format, index, and atomic activation.
7. `src/skill/organization.py` and `organization_runtime.py`: tree structure, shared boards, and the unified runtime; task, tool, and selection details use the same file prefix.
8. `src/core/records.py` and `src/core/user.py`: record contracts, user scope, audit redaction, and retention.
9. `src/adapter/`: CLI, storage, database, workspace, and process adapters last.

## 调试一轮运行 / Inspect One Run

```python
from core.provider import MockModel
from super_agent import Agent

events = []
agent = Agent(MockModel("answer"))
agent.add_event_listener(events.append)
result = agent.run("question")
print(result.run_id, [event.event_type for event in events])
```

事件是观察接口，不是第二套执行器。需要持久化时，给 Agent 传入实现 `append/read/delete` 的记录后端。

Events are an observation interface, not a second executor. For persistence, pass a backend implementing `append`, `read`, and `delete`.
