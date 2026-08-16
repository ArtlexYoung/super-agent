# Skill

## 最小格式 / Minimal Format

Skill 使用 Markdown 正文和 TOML front matter，中心 `SkillLibrary` 统一读取所有类型。

Skills use Markdown bodies with TOML front matter; the central `SkillLibrary` reads every type.

```markdown
+++
name = "research"
type = "prompt"
description = "研究问题并整理证据"
version = "1.0.0"
created_by = "user"
agent_can_update = false
categories = ["research"]
requires = ["search_documents"]
optional_tools = ["open_browser"]
+++

先澄清目标，再按证据组织结论。
```

`type` 是分类，不是隐藏的执行器。Skill 文本不能直接创建工具；工具必须由可信 Python 代码注册。

`type` is a category, not a hidden executor. Skill text cannot create tools directly; trusted Python code must register tools.

`requires` 中任一工具缺失时激活失败并回滚；`optional_tools` 中已经注册的工具会随 Skill 挂载，缺失项不会阻断纯方法内容。

Activation rolls back when any `requires` tool is missing. Registered `optional_tools` mount with the Skill, while absent optional tools do not block the method itself.

## 渐进式披露 / Progressive Disclosure

运行先看有界索引，再用 `preview` 或 Skill 工具读取正文。正文过长时返回稳定的缓存路径、偏移量和哈希，模型可以显式读取下一页。

The run starts with a bounded index, then uses `preview` or Skill tools to read the body. Large bodies return a stable cache path, offset, and hash so the model can explicitly request the next page.

```python
from pathlib import Path
from skill.library import SkillLibrary

library = SkillLibrary((Path("skills"),))
print(library.list_skills(page=1, page_size=20).to_dict())
page = library.disclose("prompt:research", max_characters=1200)
print(page.cache_path, page.sha256)
print(library.read_disclosed(page.cache_path, offset=page.next_offset or 0).to_dict())
```

`preview`、`disclose`、缓存读取和激活共用同一个 Library，不允许各功能维护自己的披露路径。

`preview`, `disclose`, cache reads, and activation share one Library; features do not maintain separate disclosure paths.

实际分页和缓存由 Runtime 的同一个 `DisclosureStore` 完成。文件、记忆和子 Agent 结果作为工具输出过大时，也会得到同样的 `read_disclosed_content` 读取工具，而不是被静默截断。

Runtime's single `DisclosureStore` performs the actual paging and caching. Large file, memory, and subagent tool results receive the same `read_disclosed_content` tool instead of being silently truncated.

## 更新与隔离 / Updates and Scope

Agent 自建 Skill 会记录 `created_by = "agent"`，并默认允许该 Agent 更新；用户 Skill 默认不允许 Agent 修改。更新、派生、删除都需要明确调用和当前哈希。

Agent-created Skills record `created_by = "agent"` and allow that Agent to update by default; user Skills are read-only to the Agent by default. Update, derive, and delete require explicit calls and the current hash.

Skill Library 按用户和 Agent 建立作用域视图。共享目录可以复用，写入覆盖层和缓存仍然隔离。

The Library creates a view per user and Agent. A shared root may be reused, while writable overlays and caches remain isolated.
