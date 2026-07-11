# 中心化渐进式披露实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 v0.0.17 中删除所有旁路 Skill 读取，让全部 Skill 生命周期统一经过中心渐进式披露核心。

**Architecture:** `ProgressiveDisclosureCore` 扫描一次并持有不可变 `SkillIndex`；`SkillDisclosure` 分阶段读取 manifest、instructions 和 kind configuration。执行器只消费披露结果，CLI 与 macOS 只消费核心 JSON 输出。

**Tech Stack:** Python 3.11+ 标准库、TOML、JSON/JSONL、Swift 5.9/SwiftUI。

## Global Constraints

- 版本保持 `0.0.x`，本版本为 `0.0.17`。
- Python runtime 不增加第三方依赖。
- 删除旧接口，不做兼容层。
- 每个版本只提交一个完整本地 commit。
- 保留用户未暂存的 `.gitignore` 修改。
- 公开函数名明确直白；`Agent.add_subagent` 保持不变。

---

### Task 1: 中心数据模型与唯一 source parser

**Files:**
- Create: `src/skill/disclosure/__init__.py`, `models.py`, `source.py`
- Modify: `src/skill/manifest.py`, `src/skill/__init__.py`
- Test: `tests/skills/test_disclosure_core.py`, `tests/skills/test_manifest.py`

**Interfaces:**
- Produces: `SkillReference`, `SkillSource`, `SkillIndexEntry`, `SkillIndex`, `SkillValidationIssue`。
- Produces: `read_skill_sources(skill_roots, disabled_names)`，唯一允许读取 Skill TOML 的函数。

- [x] 写失败测试：四种 kind 被一次解析；重复 `kind:name`、缺配置表和歧义名称明确报错。
- [x] 运行定向测试，确认因新类型和 parser 不存在而失败。
- [x] 实现纯 dataclass manifest 与中心 parser，移除 `SkillManifest.load_from_file()`。
- [x] 运行定向测试并通过。

### Task 2: 索引、选择、缓存与历史核心

**Files:**
- Create: `src/skill/disclosure/core.py`, `store.py`
- Delete: `src/skill/disclosure.py`, `src/skill/loader.py`
- Test: `tests/skills/test_disclosure_core.py`, `tests/skills/test_disclosure.py`

**Interfaces:**
- Produces: `ProgressiveDisclosureCore` 六个公开方法。
- Produces: `SkillDisclosure.read_manifest()`, `read_instructions()`, `read_kind_configuration()`。

- [x] 写失败测试：全 kind 索引、显式/触发选择、依赖解析、分阶段文件、缓存命中、历史和路径越界。
- [x] 运行定向测试，确认旧实现不能满足行为。
- [x] 实现不可变快照、`kind:name` 缓存目录、SHA-256 缓存和运行事件镜像。
- [x] 删除旧 Loader/Disclosure 并迁移公共导出。
- [x] 运行披露测试并通过。

### Task 3: 四种 kind 只消费披露结果

**Files:**
- Modify: `src/skill/kinds/mcp.py`, `memory.py`, `workflow.py`, `src/skill/kinds/__init__.py`
- Test: `tests/skills/test_mcp.py`, `test_memory.py`, `test_workflow.py`, `test_memory_workflow_kinds.py`

**Interfaces:**
- Produces: `create_mcp_server_from_skill_disclosure()`。
- Produces: `create_memory_from_skill_disclosure()`。
- Produces: `create_workflow_from_skill_disclosure()`。

- [x] 写失败测试：执行器不接收路径，全部从 `SkillDisclosure` 读取 manifest/configuration。
- [x] 运行定向测试并确认失败。
- [x] 删除三个 kind 中的 TOML 读取和旧文件工厂。
- [x] 实现披露输入工厂并运行定向测试通过。

### Task 4: Agent、工具与 benchmark 统一

**Files:**
- Modify: `src/core/agent.py`, `tools.py`, `benchmark.py`, `core/__init__.py`, `super_agent.py`
- Test: `tests/agent/test_agent_config.py`, `test_tools.py`, `test_benchmark.py`, `test_run.py`

**Interfaces:**
- Produces: `create_progressive_disclosure_for_agent_config(config, run_context=None)`。
- Tool names: `list_skills`, `read_skill_manifest`, `read_skill_instructions`, `read_skill_configuration`, `read_disclosed_content`, MCP/memory/subagent 执行工具。

- [x] 写失败测试：一次运行只准备一次索引；四种 kind 事件进入同一历史；工具返回核心缓存路径。
- [x] 运行定向测试并确认失败。
- [x] Agent、SkillTools、benchmark 改为共享同一个 core 实例。
- [x] 删除旧 `create_skill_loader_for_agent_config` 和所有直接 loader 调用。
- [x] 运行 agent 测试并通过。

### Task 5: CLI、依赖、进化与包管理统一

**Files:**
- Modify: `src/cli_commands/skills.py`, `memory.py`, `benchmark.py`
- Modify: `src/skill/ecosystem/lock.py`, `package.py`, `evolution/candidate.py`, `manager.py`, `evaluation.py`
- Delete: `src/skill/ecosystem/resolver.py`
- Test: `tests/commands/test_cli.py`, `tests/skills/test_composition.py`, `test_evolution.py`, `test_packages.py`

**Interfaces:**
- Adds CLI: `super-agent skills index --config agent.toml --output json`。
- `SkillPackageManager` 和 `SkillEvolutionManager` 接收 `ProgressiveDisclosureCore`。

- [x] 写失败测试：JSON index 覆盖全部 kind 和动态保鲜度；graph/lock/evolution/package 均通过 core。
- [x] 运行定向测试并确认失败。
- [x] 迁移所有消费者，删除 resolver 和 standalone validate/explain 逻辑。
- [x] 运行 CLI、生态和进化测试并通过。

### Task 6: macOS 只消费中心 JSON index

**Files:**
- Create: `src/frontend/mac/Sources/SuperAgentMac/Support/SkillIndexClient.swift`
- Modify: `ChatStore.swift`, `ChatModels.swift`
- Delete: `Support/SkillManifestScanner.swift`

**Interfaces:**
- Consumes: `super-agent skills index --output json`。
- Produces: `[SkillManifestChoice]`，字段直接来自 Python 核心。

- [x] 添加可解码中心 index 的 Swift DTO。
- [x] 实现异步 `SkillIndexClient`，复用 runtime 命令选择规则。
- [x] ChatStore 刷新配置时调用中心 CLI，删除本地 TOML/保鲜度扫描。
- [x] 运行 `swift build --package-path src/frontend/mac` 并通过。

### Task 7: 文档、版本和完整验证

**Files:**
- Modify: `README.md`, `pyproject.toml`, `examples/`, 本计划。

- [x] README 说明唯一核心、四阶段披露、缓存路径、CLI JSON index 和不兼容迁移。
- [x] 更新版本到 `0.0.17`，迁移全部测试 fixture。
- [x] 运行全量 Python 测试、compileall、Swift build、wheel 构建/隔离导入、CLI run/index/benchmark 冒烟。
- [x] 扫描源码，确认只有 `skill/disclosure/source.py` 解析 Skill TOML，Swift 不再扫描 manifest。
- [x] 清理构建产物，运行 `git diff --check`。
- [x] 排除 `.gitignore` 后提交 `refactor(skill): 发布 v0.0.17 中心披露核心`。
