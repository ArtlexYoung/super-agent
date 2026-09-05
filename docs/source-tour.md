# 源码阅读路径 / Source Tour

建议从公开入口开始，而不是从适配器或内置 Skill 目录开始。

Start at the public entry point instead of beginning with adapters or builtin Skill files.

1. `src/super_agent/__init__.py`：先看公开 `Agent` 如何组合模型、插件、存储、组和子 Agent。
2. `src/super_agent/skill/agent_builder.py`：跟踪一次公开调用如何装配成唯一的 `RunInput`。
3. `src/super_agent/core/run.py`：查看唯一的流式模型与工具循环；数据类型在 `core/model.py`，供应商转换在 `core/provider.py`。
4. `src/super_agent/core/resources.py`：查看统一分页、缓存路径、历史和每轮预算；标准 Skill 解析、去重与快照位于 `skill/document.py` 和 `skill/library.py`。
5. `src/super_agent/skill/organization.py` 与 `organization_runtime.py`：查看 Agent 树、共享板和团队运行；任务、工具和选择细节位于同名前缀文件。
6. `src/super_agent/core/records.py` 与 `core/user.py`：查看统一记录、用户作用域、审计脱敏和保留策略。
7. `src/super_agent/adapter/`：最后阅读 CLI、存储、数据库、代码工作区和进程适配器。

1. `src/super_agent/__init__.py`: start with how public `Agent` composes models, plugins, storage, groups, and subagents.
2. `src/super_agent/skill/agent_builder.py`: follow one public call as it becomes the only `RunInput`.
3. `src/super_agent/core/run.py`: inspect the single streaming model/tool loop; neutral types live in `core/model.py`, and provider conversion lives in `core/provider.py`.
4. `src/super_agent/core/resources.py`: inspect shared paging, cache paths, history, and per-run budgets; standard Skill parsing, deduplication, and snapshots live in `skill/document.py` and `skill/library.py`.
5. `src/super_agent/skill/organization.py` and `organization_runtime.py`: inspect the Agent tree, shared boards, and team runtime; task, tool, and selection details use the same file prefix.
6. `src/super_agent/core/records.py` and `core/user.py`: inspect unified records, user scope, audit redaction, and retention.
7. `src/super_agent/adapter/`: read CLI, storage, database, code workspace, and process adapters last.

## 调试一轮运行 / Inspect One Run

```python
from super_agent.core.provider import MockModel
from super_agent import Agent

events = []
agent = Agent(MockModel("answer"))
agent.add_event_listener(events.append)
result = agent.run("question")
print(result.run_id, [event.event_type for event in events])
```

事件是观察接口，不是第二套执行器。需要持久化时，给 Agent 传入实现 `append/read/delete` 的记录后端。

Events are an observation interface, not a second executor. For persistence, pass a backend implementing `append`, `read`, and `delete`.
