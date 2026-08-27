# 插件与 Skill / Plugins and Skills

## 两个概念 / Two Concepts

Skill 是一份被动方法或内容，继续完全采用 [Agent Skills](https://agentskills.io/specification) 的 `SKILL.md`、YAML front matter 和 Markdown 正文。

A Skill is one passive method or content unit and continues to use the [Agent Skills](https://agentskills.io/specification) `SKILL.md`, YAML front matter, and Markdown body.

插件是一个或多个 Skill 的安装、版本、依赖、去重和外部进化权限边界。它不加载 Python 代码，也不是第二套工作流格式。

A plugin is the installation, version, dependency, deduplication, and external evolution-authority boundary for one or more Skills. It loads no Python code and is not a second workflow format.

```text
plugins/
└── research/
    ├── plugin.toml
    ├── SKILL.md
    ├── skills/
    │   └── evidence/
    │       └── SKILL.md
    └── references/
        └── sources.md
```

## 插件清单 / Plugin Manifest

```toml
schema = 1
id = "local/research"
version = "1.0.0"
requires = ["super-agent/common"]
```

`id` 是稳定的小写身份，支持用 `/` 分段；`requires` 使用插件 ID，不带 `plugin:` 前缀。插件入口引用为 `plugin:local/research`，入口 Skill 为 `skill:local/research/main`，成员引用为 `skill:local/research/evidence`。

`id` is a stable lowercase slash-separated identity; `requires` contains plugin IDs without the `plugin:` prefix. The plugin reference is `plugin:local/research`, its entry Skill is `skill:local/research/main`, and a member is `skill:local/research/evidence`.

单个标准 Agent Skill 可以不带 `plugin.toml`。此时目录名和 Skill `name` 形成隐式插件身份，因此其他标准 Skill 生态的目录可以直接读取，无需转换或私有 front matter。

A single standard Agent Skill may omit `plugin.toml`. Its directory and Skill `name` then form an implicit plugin identity, so directories from other standard Skill ecosystems load directly without conversion or private front matter.

## Skill 格式 / Skill Format

```markdown
---
name: evidence
description: 研究问题并整理证据；需要比较来源或形成带依据结论时使用。
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

`super-agent-type` 和 categories 只用于索引。`requires` 中任一可信工具缺失时，整次激活原子回滚；已注册的 optional tools 会挂入运行，缺失的可选项不阻断纯方法内容。`includes` 必须使用规范 Skill 引用。

`super-agent-type` and categories affect only indexing. A missing trusted tool in `requires` rolls back the whole activation; registered optional tools join the run while absent optional tools do not block method-only content. `includes` must use canonical Skill references.

跨插件 include 只有在当前插件的 `requires` 已声明目标插件时才有效。插件依赖缺失或形成循环会在建立快照时直接失败。

A cross-plugin include is valid only when the current plugin declares the target plugin in `requires`. Missing or cyclic plugin dependencies fail while building the snapshot.

Skill 不再包含“由谁创建”或“允许 Agent 更新”等自授权字段。写权限只来自外部 `[evolution]` 配置或 `Agent.allow_plugin_to_evolve`、`Agent.allow_skill_to_evolve`。

A Skill no longer carries self-authorizing creator or Agent-update fields. Write authority comes only from external `[evolution]` configuration or `Agent.allow_plugin_to_evolve` and `Agent.allow_skill_to_evolve`.

## 发现与去重 / Discovery and Deduplication

`PluginCatalog` 扫描只读根和用户-Agent 可写覆盖层，按以下规则建立一个快照：

`PluginCatalog` scans read-only roots and the user-Agent writable overlay, then builds one snapshot under these rules:

1. 相同插件 ID、版本和完整包 SHA-256 跨来源只保留一个逻辑实例，并记录全部来源。
2. 相同 ID 但版本或内容不同直接报冲突，不按目录顺序偷偷覆盖。
3. 可写覆盖层必须携带原内容的 `base_hash`，基线变化后拒绝应用。
4. 每个规范 Skill 引用只有一个所有者；插件依赖复用 Skill，不复制文件。

1. Matching plugin ID, version, and full-package SHA-256 across sources becomes one logical instance with every source recorded.
2. A shared ID with divergent version or content fails instead of silently winning by path order.
3. A writable overlay carries the original `base_hash` and is rejected after its baseline changes.
4. Every canonical Skill reference has one owner; plugin dependencies reuse Skills instead of copying files.

内置 `super-agent/code` 依赖 `super-agent/common`，因此复用 common 的多 Agent Skill，而不是安装第二份。

The builtin `super-agent/code` plugin depends on `super-agent/common`, so it reuses the common multi-Agent Skill instead of installing another copy.

## 渐进式披露 / Progressive Disclosure

```python
from pathlib import Path
from skill.library import PluginCatalog

catalog = PluginCatalog((Path("plugins"),))
print(catalog.list_plugins().to_dict())
print(catalog.list_skills(plugin="plugin:local/research").to_dict())

page = catalog.disclose_skill(
    "skill:local/research/evidence",
    max_characters=1200,
)
print(page.cache_path, page.sha256)
print(catalog.read_disclosed(page.cache_path, offset=page.next_offset or 0).to_dict())
```

插件索引、Skill 索引、正文、资源、缓存历史和激活都经过同一个 Catalog 与 `DisclosureStore`。相同正文按 SHA-256 只保存一份缓存对象，不同逻辑引用仍各自保留披露历史。

Plugin indexes, Skill indexes, bodies, resources, cache history, and activation all pass through one Catalog and `DisclosureStore`. Identical bodies share one SHA-256-addressed cache object while each logical reference retains its own disclosure history.

`references/`、`scripts/`、`assets/` 和其他文件会随目录或 ZIP 打包安装。模型只能通过 `read_skill_resource` 分页读取安全的 UTF-8 文本；安装或激活永远不会执行 `scripts/`。

`references/`, `scripts/`, `assets/`, and other files remain in directory or ZIP packages. The model can only page safe UTF-8 text through `read_skill_resource`; installation and activation never execute `scripts/`.

## 激活和快照 / Activation and Snapshots

`activate_plugin` 先递归激活插件依赖，再激活入口 Skill。`activate_skill` 递归处理 includes。工具缺失、内容过大或循环会回滚本次激活前添加的指令、工具、账本和活动引用。

`activate_plugin` activates plugin dependencies before its entry Skill. `activate_skill` recursively handles includes. Missing tools, oversized content, or cycles roll back instructions, tools, ledger entries, and active references added by that activation.

每次顶层运行保存不可变插件快照。更新成功后调用 `catalog.refresh()`，新内容只在下一次运行可见，避免一次模型调用中方法发生漂移。

Every top-level run records an immutable plugin snapshot. Call `catalog.refresh()` after a successful update; new content becomes visible only to the next run, preventing method drift during one model call.

## 内置方法 / Builtin Methods

- `plugin:super-agent/common`：通用证据驱动任务入口。
- `skill:super-agent/common/multi-agent`：队列、等待唤醒和共享板方法。
- `skill:super-agent/common/review`：独立多 Agent 检视与交叉验证。
- `plugin:super-agent/code`：仓库编码方法。
- `skill:super-agent/code/deep-optimization`：嵌套批次、轮换模型和实测优化。

- `plugin:super-agent/common`: general evidence-driven task entry.
- `skill:super-agent/common/multi-agent`: queues, event-driven waiting, and shared boards.
- `skill:super-agent/common/review`: independent multi-Agent review and cross-checking.
- `plugin:super-agent/code`: repository coding method.
- `skill:super-agent/code/deep-optimization`: nested batches, model rotation, and measured optimization.
