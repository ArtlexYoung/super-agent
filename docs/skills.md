# Skill

## 标准格式 / Standard Format

Super Agent 直接使用 [Agent Skills](https://agentskills.io/specification) 目录格式：每个 Skill 是一个以 Skill 名称命名的目录，入口固定为 `SKILL.md`，内容为 YAML front matter 加 Markdown 正文。

Super Agent uses the [Agent Skills](https://agentskills.io/specification) directory format directly: each Skill is a name-matching directory with a `SKILL.md` entry containing YAML front matter and a Markdown body.

```text
skills/
└── research/
    └── SKILL.md
```

最小 Skill 只需要两个标准字段。没有 Super Agent 扩展时，它会作为用户拥有、不可由 Agent 更新的 `prompt` Skill 加载。

A minimal Skill needs only the two standard fields. Without Super Agent extensions, it loads as a user-owned, non-Agent-updatable `prompt` Skill.

```markdown
---
name: research
description: 研究问题并整理证据；需要调查、比较来源或形成带依据结论时使用。
---

先澄清目标，再按证据组织结论。
```

目录名必须与 `name` 一致。旧的平铺 `.md` 和 TOML front matter 不再读取，也没有兼容转换层。

The directory name must match `name`. Legacy flat `.md` files and TOML front matter are no longer read, and no compatibility converter remains.

## Super Agent 扩展 / Super Agent Extensions

Agent Skills 规范要求 `metadata` 是字符串到字符串的映射。高级行为使用带命名空间的字符串字段，列表使用 JSON 字符串；标准顶层字段保持不变。

The Agent Skills specification defines `metadata` as a string-to-string map. Advanced behavior uses namespaced string fields, with lists encoded as JSON strings, while standard top-level fields remain unchanged.

```yaml
metadata:
  super-agent-type: "task"
  super-agent-version: "1.0.0"
  super-agent-created-by: "user"
  super-agent-agent-can-update: "false"
  super-agent-categories: '["research"]'
  super-agent-requires: '["search_documents"]'
  super-agent-optional-tools: '["open_browser"]'
  super-agent-includes: '["prompt:evidence"]'
```

`super-agent-type` 只是分类，不是隐藏执行器。`super-agent-requires` 中任一可信 Python 工具缺失时，激活失败并回滚；`super-agent-optional-tools` 中已经注册的工具会被挂载，缺失项不阻断纯方法内容。

`super-agent-type` is a category, not a hidden executor. Activation rolls back when any trusted Python tool in `super-agent-requires` is missing; registered tools from `super-agent-optional-tools` mount, while absent optional tools do not block the method itself.

## 渐进式披露 / Progressive Disclosure

运行先看有界索引，再用 `preview` 或 Skill 工具读取正文。正文过长时返回稳定的缓存路径、偏移量和哈希，模型可以显式读取下一页。

The run starts with a bounded index, then uses `preview` or Skill tools to read the body. Large bodies return a stable cache path, offset, and hash so the model can explicitly request the next page.

```python
from pathlib import Path
from skill.library import SkillLibrary

library = SkillLibrary((Path("skills"),))
print(library.list_skills(page=1, page_size=20).to_dict())
page = library.disclose("research", max_characters=1200)
print(page.cache_path, page.sha256)
print(library.read_disclosed(page.cache_path, offset=page.next_offset or 0).to_dict())
```

`preview`、`disclose`、缓存读取和激活共用同一个 Library。文件、记忆和子 Agent 结果过大时也使用 Runtime 的同一个 `DisclosureStore`，不会被静默截断。

`preview`, `disclose`, cache reads, and activation share one Library. Large files, memories, and subagent results also use Runtime's single `DisclosureStore` and are never silently truncated.

## 标准资源 / Standard Resources

`references/`、`scripts/`、`assets/` 和其他 Skill 内文件会随目录或 ZIP 一起打包和安装。模型通过 `read_skill_resource` 显式分页读取 UTF-8 文本资源；越界路径、软链接、超大文件和二进制内容直接失败。

`references/`, `scripts/`, `assets/`, and other in-Skill files are preserved when directories or ZIP files are packed and installed. The model explicitly pages through UTF-8 text via `read_skill_resource`; escaping paths, symbolic links, oversized files, and binary content fail directly.

资源始终是被动数据。安装或激活 Skill 不会执行 `scripts/`，执行仍需用户配置并授权的可信工具。

Resources always remain passive data. Installing or activating a Skill never executes `scripts/`; execution still requires a trusted tool configured and authorized by the user.

## 多 Agent 检视 / Multi-Agent Review

内置 `task:common-multi-review` 让至少两个不同 Agent 独立检查同一份材料，再由另一轮 Agent 交叉验证发现。它不是执行者自检，也不以简单投票替代证据；只有仍有重大争议的发现才进入决策组。

The builtin `task:common-multi-review` assigns the same artifact to at least two distinct Agents, then cross-checks findings in another Agent pass. It is neither executor self-review nor a simple vote; only consequential findings that remain disputed enter a decision quorum.

同一批任务先完成全部分配，再并行启动。Agent 或模型多样性不足时，整批任务保持未启动并明确失败，不会部分派发或退化成单 Agent 检查。

The complete batch is assigned before any task starts in parallel. Insufficient Agent or model diversity leaves the whole batch untouched and fails explicitly instead of partially dispatching or degrading to one-Agent review.

## 更新与隔离 / Updates and Scope

Agent 自建 Skill 会记录 `super-agent-created-by = "agent"`，并默认允许该 Agent 更新。用户 Skill 默认不允许 Agent 修改；更新、派生、删除都需要明确调用和当前哈希。

Agent-created Skills record `super-agent-created-by = "agent"` and allow that Agent to update by default. User Skills are read-only to the Agent by default; update, derive, and delete require an explicit call and current hash.

Skill Library 按用户和 Agent 建立作用域视图。共享目录可以复用，写入覆盖层和披露缓存仍然隔离。

The Library creates a view per user and Agent. Shared roots may be reused, while writable overlays and disclosure caches remain isolated.
