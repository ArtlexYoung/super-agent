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

内置插件都位于 `src/super_agent/skill/builtin/plugins/super-agent/`，对应正文位于 `src/super_agent/skill/builtin/skills/super-agent/`。它们是可选方法集合，不会因为被发现而自动执行。

Builtin plugins live under `src/super_agent/skill/builtin/plugins/super-agent/`, with their Skill bodies under `src/super_agent/skill/builtin/skills/super-agent/`. They are optional method sets and never execute merely because they were discovered.

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
  super-agent-includes: '["skill:super-agent/session/conversation"]'
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
runtime_extension = "context-window"
```

`entry_skill` 是插件入口；`skills` 是附加方法；`included_plugins` 递归复用其他插件引用。必需 MCP 未由代码显式绑定时激活失败，可选 MCP 缺失不会自动降级或建立连接。

`runtime_extension` 只声明一个由可信代码预先注册的 Runtime 扩展名称。清单不会导入或执行任意代码；未知扩展在运行开始前直接失败。

`entry_skill` is the plugin entry; `skills` lists extra methods; `included_plugins` recursively reuses another plugin's references. Activation fails when required MCP is not explicitly bound by code. Missing optional MCP never creates a connection or hidden fallback.

`runtime_extension` declares only a Runtime extension name registered by trusted code. A manifest never imports or executes arbitrary code; an unknown extension fails before the run starts.

## 插件开发检查 / Plugin Development Checks

插件目录只能包含 `plugin.toml`，正文 Skill 和 MCP 描述必须放在中央 `skills/`、`mcps/` 目录并使用规范引用。清单 ID 必须匹配目录；入口、成员、嵌套插件和 MCP 引用都必须存在；同一列表不得重复，必需与可选 MCP 不得重叠，插件包含关系不得成环。

A plugin directory contains only `plugin.toml`; Skill bodies and MCP definitions stay in central `skills/` and `mcps/` directories and use canonical references. Manifest IDs match their directories; entry, member, included-plugin, and MCP references exist; lists contain no duplicates, required and optional MCP sets do not overlap, and plugin includes remain acyclic.

开发时运行 `super-agent check --config common.toml --output json` 验证实际资源库。发布时运行 `python3.11 scripts/verify_release.py --version <version>`，它还检查内置插件集合、版本一致性和物理资源唯一性。

Run `super-agent check --config common.toml --output json` while developing to validate the real library. At release time, `python3.11 scripts/verify_release.py --version <version>` also checks the builtin plugin set, version alignment, and physical resource uniqueness.

## 损坏资源 / Damaged Resources

发现阶段可定位到规范目录的格式错误（Skill frontmatter、UTF-8、skill.toml、plugin.toml、mcp.toml 或字段类型）会形成 `invalid_format` 诊断。对应引用不进入健康索引；依赖它的 Skill 和插件按传递关系停用，记录 `blocked_dependency` 及直接不可用依赖。其他健康方法仍可披露和运行。可选 MCP 的连接仍是可选的，但声明的描述资源必须有效，损坏描述同样阻塞引用插件。

Discovery isolates format errors with a canonical directory identity: Skill frontmatter, UTF-8, skill.toml, plugin.toml, mcp.toml, or field types. They produce `invalid_format` diagnostics and leave the healthy indexes. Dependent Skills and plugins are disabled transitively with `blocked_dependency` and direct unavailable dependencies; unrelated healthy methods remain usable. An optional MCP connection remains optional, but its declared definition must be valid.

```python
from super_agent.skill.library import AgentLibrary, ResourceUnavailableError

library = AgentLibrary(("library",))
report = library.list_diagnostics(page=1, page_size=20).to_dict()
print(report)  # key / path / code / message / sha256 / dependencies
try:
    method = library.find_skill("skill:local/research")
except ResourceUnavailableError as error:
    print(error.reference, [item.to_dict() for item in error.diagnostics])

# 在原位置修复文件之后，显式重建下一份快照。
library.refresh()
print(library.list_diagnostics().to_dict())
```

模型通过 `list_resource_diagnostics` 分页查询同一份诊断；启动索引只在出现问题时附加诊断首页。`super-agent check --config common.toml --output json --page 1 --page-size 20` 返回 `resources_ready`、`resource_diagnostics` 和健康资源数量；模型未配置或任何资源受阻时退出码为 1，身份冲突、权限等全局错误仍为 2。发行门禁拒绝任何内置资源诊断，不把局部隔离当作发行通过。

Models page the same diagnostics through `list_resource_diagnostics`; startup indexes add a diagnostics page only when problems exist. `super-agent check --config common.toml --output json --page 1 --page-size 20` reports `resources_ready`, `resource_diagnostics`, and healthy resource counts. Missing model configuration or unavailable resources returns exit code 1; global errors such as identity conflicts or permission failures still return 2. The release gate rejects every builtin diagnostic.

诊断与资源摘要一起固定在 `LibrarySnapshot` 并参与快照身份；消息最多 1,024 字符，不复制方法正文。修复文件不会改变已冻结快照，显式 `refresh()` 后重试。同一引用的任一来源损坏都会停用该引用，含共享副本和用户覆盖层，不自动回退旧内容。明确选中受阻方法会抛 `ResourceUnavailableError`，不调用模型或替换指定方法。

Diagnostics and content digests are frozen in `LibrarySnapshot` and contribute to its identity. Messages are limited to 1,024 characters and do not copy method bodies. Repairs leave frozen snapshots unchanged; retry after explicit `refresh()`. A damaged source disables the entire reference, including shared copies and user overlays, with no automatic fallback. Explicitly selecting an unavailable method raises `ResourceUnavailableError` before any model call.

规范目录身份不明确、不同有效来源的身份冲突、过期覆盖基线、普通缺失引用、引用循环、文件系统/扫描错误和关键记录失败仍直接报错，不纳入局部格式隔离。损坏资源可能隐藏尚未解析的引用，因此移除资源要求完整健康索引，先修复并刷新再删除；健康资源的创建和更新仍沿用原写入权限与身份检查。此边界只诊断方法格式，不证明方法正确或提供任意 Python 代码的崩溃隔离。

Ambiguous directory identities, conflicting valid sources, stale overlay baselines, ordinary missing references, cycles, filesystem/scan errors, and record failures still fail directly. Damaged resources may hide unparsed references, so removal requires a complete healthy index after repair and refresh. Creating and updating healthy resources retain existing write permissions and identity checks. This boundary diagnoses method formats; it proves neither method correctness nor crash isolation for arbitrary Python code.

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

from super_agent.skill.library import AgentLibrary
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
