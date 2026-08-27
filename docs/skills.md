# 中央资源库 / Central Agent Library

`AgentLibrary` 是 Skill、MCP 描述和插件的唯一发现、去重、披露、快照与写入入口。

`AgentLibrary` is the single discovery, deduplication, disclosure, snapshot, and write entry point for Skills, MCP definitions, and plugins.

```text
library/
├── skills/
│   └── local/
│       ├── research/SKILL.md
│       └── evidence/SKILL.md
├── mcps/
│   └── example/search/mcp.toml
└── plugins/
    └── local/research/plugin.toml
```

Skill 和 MCP 是中央资源。插件只保存引用，不含 `SKILL.md`、MCP 命令、地址或密钥。

Skills and MCP definitions are central resources. A plugin stores references only and contains no `SKILL.md`, MCP command, endpoint, or secret.

## Skill

```markdown
---
name: evidence
description: 研究问题并整理证据；需要形成有依据的结论时使用。
metadata:
  super-agent-type: "task"
  super-agent-version: "1.0.0"
  super-agent-categories: '["research"]'
  super-agent-requires: '["search_documents"]'
  super-agent-optional-tools: '["open_browser"]'
  super-agent-includes: '["skill:super-agent/common/conversation"]'
---

先澄清目标，再按证据组织结论。
```

目录 `skills/local/evidence/` 形成引用 `skill:local/evidence`。`includes` 只能使用规范 Skill 引用。缺少任一 `requires` 工具时激活会原子回滚；缺少可选工具不阻断纯方法内容。

Directory `skills/local/evidence/` becomes `skill:local/evidence`. `includes` accepts canonical Skill references only. A missing required tool rolls activation back atomically; a missing optional tool does not block method-only content.

标准 Skill 可以带 `references/`、`scripts/`、`assets/` 等资源，但安装和激活不会执行脚本。模型只能通过有界的 `read_skill_resource` 读取 UTF-8 文本。

A standard Skill may carry `references/`, `scripts/`, `assets/`, and other resources, but installation and activation never execute scripts. The model can read UTF-8 text only through bounded `read_skill_resource` calls.

## MCP 描述 / MCP Definition

```toml
schema = 1
id = "example/search"
version = "1.0.0"
description = "Search service"
tools = ["search"]
```

目录 `mcps/example/search/` 形成 `mcp:example/search`。此文件只描述身份和预期工具。可信代码必须调用 `Agent.connect_mcp_server()` 并为每个工具声明副作用；扫描、插件激活和配置加载均不会启动进程或联网。实际服务暴露的工具与 `tools` 不一致时直接失败。

Directory `mcps/example/search/` becomes `mcp:example/search`. This file describes identity and expected tools only. Trusted code must call `Agent.connect_mcp_server()` and declare every tool effect; scanning, plugin activation, and configuration loading never start a process or connect to a network. A mismatch between live and declared tools fails directly.

## 插件 / Plugin

```toml
schema = 1
id = "local/research"
version = "1.0.0"
description = "Research methods"
entry_skill = "skill:local/research"
skills = ["skill:local/evidence"]
included_plugins = ["plugin:super-agent/common"]
required_mcp_servers = ["mcp:example/search"]
optional_mcp_servers = []
```

`entry_skill` 是插件入口；`skills` 是附加方法；`included_plugins` 递归复用其他插件引用。必需 MCP 未由代码显式绑定时激活失败，可选 MCP 缺失不会自动降级或建立连接。

`entry_skill` is the plugin entry; `skills` lists extra methods; `included_plugins` recursively reuses another plugin's references. Activation fails when required MCP is not explicitly bound by code. Missing optional MCP never creates a connection or hidden fallback.

## 去重与冲突 / Deduplication and Conflicts

1. 同类型、ID、版本和内容哈希相同：合并为一个逻辑对象并记录全部来源。
2. 同类型和 ID 相同，但版本或内容不同：直接报冲突，不按路径顺序覆盖。
3. 多个插件引用同一 Skill 或 MCP：只引用中央对象，不复制文件。
4. Skill 正文披露缓存按 SHA-256 物理复用，同时保留每个逻辑引用的历史。
5. 删除被插件、Skill include 或其他依赖引用的资源：直接失败。

1. Matching type, ID, version, and content hash merge into one logical object with every source recorded.
2. A shared type and ID with a different version or body fails instead of winning by path order.
3. Plugins referring to one Skill or MCP share the central object without copying files.
4. Skill body disclosure caches deduplicate by SHA-256 while retaining logical-reference history.
5. Removing a resource still referenced by a plugin, Skill include, or dependency fails.

## 使用 / Usage

```python
from pathlib import Path

from skill.library import AgentLibrary
from super_agent import Agent

library = AgentLibrary(
    (Path("library"),),
    writable_root=Path(".super-agent/library"),
    cache_root=Path(".super-agent/cache"),
)
agent = Agent(model)
agent.use_agent_library(library)
agent.enable_plugin("plugin:local/research")
```

`list_plugins`、`list_skills` 和 `list_mcp_servers` 只返回有界索引。`read_*` 只披露内容，不激活。运行开始时 `LibrarySnapshot` 固定三类资源的版本与 SHA-256；更新只对下一次顶层运行可见。

`list_plugins`, `list_skills`, and `list_mcp_servers` return bounded indexes only. `read_*` discloses without activation. At run start, `LibrarySnapshot` freezes versions and SHA-256 values for all three resource types; updates become visible only to the next top-level run.

共享 Skill 的更新写入用户和 Agent 隔离的中央 Skill 覆盖层，并用 `base_hash` 验证原基线。过期哈希、变化的基线、失败测试或缺少外部授权都会拒绝写入。

Updating a shared Skill writes to a user-Agent-isolated central Skill overlay and verifies the original baseline through `base_hash`. A stale hash, changed baseline, failed test, or missing external authority rejects the write.
