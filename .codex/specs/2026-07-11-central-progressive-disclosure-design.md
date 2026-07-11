# 中心化渐进式披露设计

## 目标

在 `v0.0.17` 中建立唯一的 Skill 事实源。prompt、MCP、memory、workflow、CLI、benchmark、依赖解析、进化、包管理和 macOS 前端都必须通过同一个渐进式披露核心读取 Skill，不再自行扫描目录、解析 TOML 或拼装索引。

## 当前问题

当前 `ProgressiveDisclosure` 只处理 prompt/MCP 指令，`SkillLoader` 负责另一套扫描和选择；memory、workflow、MCP 执行器再次读取 `skill.toml`；benchmark 自行构造索引；Swift 前端用字符串解析 TOML 并独立合并保鲜度。相同规则存在多个实现，已经造成缓存覆盖、口径不一致和 schema 绕过风险。

## 架构

新增 `skill.disclosure` 包，核心入口为 `ProgressiveDisclosureCore`。核心在 `prepare_skill_index()` 时扫描一次全部 Skill，生成不可变目录快照；同一次运行后续只使用该快照。Skill 安装、更新、晋升、回滚或删除后，再次调用 `prepare_skill_index()` 即重新扫描。

核心公开方法保持明确且不超过六个：

```python
core.validate_skill_sources() -> list[SkillValidationIssue]
core.prepare_skill_index() -> SkillIndex
core.select_skill_references_for_prompt(prompt, enabled_names) -> list[SkillReference]
core.open_skill(name, expected_kind=None) -> SkillDisclosure
core.read_disclosed_content(cache_path) -> str
core.read_disclosure_history() -> list[SkillDisclosureEvent]
```

`SkillDisclosure` 是绑定到单个 Skill 的分阶段读取句柄：

```python
skill.read_manifest() -> SkillManifest
skill.read_instructions() -> str
skill.read_kind_configuration() -> dict[str, object]
```

## 数据模型

- `SkillReference` 使用 `kind:name` 作为稳定键，解决同名不同 kind 的歧义。
- `SkillSource` 保存一次 TOML 解析得到的 manifest、kind 配置和源目录，仅由核心内部持有。
- `SkillIndexEntry` 保存模型和前端需要的摘要、有效保鲜度、调用统计及各披露阶段缓存路径。
- `SkillIndex` 提供明确的查找和依赖解析方法；依赖解析不再由独立 resolver 维护第二份目录。
- `SkillDisclosureEvent` 记录 schema 版本、运行编号、顺序、Skill 键、阶段、缓存路径、内容哈希和是否命中缓存。

## 披露阶段

1. `index`：一次公开全部未禁用 Skill 的摘要，不加载指令和 kind 配置。
2. `manifest`：按需缓存规范化 manifest。
3. `instructions`：按需缓存 `SKILL.md`；只有请求该阶段时读取文件。
4. `configuration`：按需缓存 `[mcp]`、`[memory]` 或 `[workflow]`；prompt 返回空对象。

缓存路径统一为：

```text
.super-agent/memory/disclosure/
  index.json
  history.jsonl
  skills/<kind>/<name>/manifest.json
  skills/<kind>/<name>/instructions.md
  skills/<kind>/<name>/configuration.json
```

写缓存时比较 SHA-256。内容未变化时复用原路径并记录 `cache_hit = true`。所有路径读取都限制在披露根目录内。

## 运行时接入

- `Agent.run()` 每次创建一个核心并准备一次索引，再通过该实例取得 workflow、memory、prompt 和 MCP。
- direct/plan 根据触发词和显式启用列表选择 prompt/MCP，随后读取对应指令。
- react/loop 首轮只得到索引路径；模型通过明确工具读取 manifest、instructions、configuration 或已缓存路径。
- MCP 执行器只接受 `SkillDisclosure`，不再提供 `load_from_file()`。
- memory/workflow 工厂只接受 `SkillDisclosure`，不再直接读取 TOML。
- 披露事件同时追加 `history.jsonl`，并在存在 `RunContext` 时镜像到运行追踪。

记忆条目、运行事件和模型输出属于运行数据，不纳入 Skill 定义缓存；memory Skill 的策略定义必须通过披露核心读取。

## 非运行时接入

- benchmark 使用核心真实索引和指令内容计算上下文，不再自行拼索引。
- CLI list、validate、explain、graph、lock 和 JSON index 都使用核心快照。
- 依赖解析、进化和包管理通过核心查找、验证 Skill；写操作后重新准备索引。
- macOS 删除 `SkillManifestScanner`，改为调用 `super-agent skills index --output json`，前端只解码核心输出。
- 有效保鲜度和运行统计在核心构建索引时合并，Swift 不再读取 `skill_stats.json`。

## 迁移和错误

本版本不保留 `SkillLoader`、旧 `ProgressiveDisclosure`、`SkillDependencyResolver` 和 Swift TOML 扫描器兼容层。非法 schema、重复 `kind:name`、名字歧义、缺少 kind 配置、依赖缺失或循环、缓存路径越界都直接失败。

## 验收

- Python 源码中除中心 source parser 外不存在 `skill.toml` 的直接 TOML 解析。
- Swift 源码中不存在 Skill manifest 文件扫描或 TOML 字符串解析。
- 四种 kind 都产生统一 index、manifest、configuration/instructions 披露和历史事件。
- Agent、CLI、benchmark、进化、包管理和桌面端均消费中心核心。
- 全量 Python 测试、Swift build、wheel 导入和 CLI 冒烟通过。

