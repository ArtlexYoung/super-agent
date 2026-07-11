# v0.0.18 兼容层与薄壳清理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除中心披露统一后残留的兼容入口和无语义薄壳，发布 `v0.0.18`。

**Architecture:** `super_agent.py` 保留为唯一公共聚合入口，内部模块直接依赖定义模块。provider、macOS 存储和序列化只接受当前格式，不做旧名称或旧文件转换。

**Tech Stack:** Python 3.11+ 标准库、Swift 5.9、TOML、JSON/JSONL。

## Global Constraints

- 版本为 `0.0.18`，不增加第三方依赖。
- 不保留本版本删除接口的兼容层。
- 保留用户未提交的 `.gitignore` 修改。
- 只创建一个完整本地版本提交，不推送。

### Task 1: 删除动态 facade

- [x] 将 `super_agent.py` 改为直接导入真实定义模块。
- [x] 将内部代码和测试改为直接导入定义模块。
- [x] 清空五个中间包的动态导出表和 `__getattr__`。
- [x] 添加中间包不再聚合业务对象的架构测试。

### Task 2: 删除旧行为兼容

- [x] 删除 `openai`、`anthropic` provider 别名并先校验 provider 名称。
- [x] 删除 macOS 旧 `state.json` 读取与转换。
- [x] 删除 README 和配置界面的旧 provider 选项。

### Task 3: 删除转换薄壳

- [x] 删除 `BenchmarkReport.to_dict()` 并直接调用 schema 序列化函数。
- [x] 删除 Swift `makeChoice()` 字段转换方法。
- [x] 要求 `SkillManifestChoice` 显式接收稳定键和功能分组。

### Task 4: 版本与验证

- [x] 更新 README、设计说明和版本号到 `0.0.18`。
- [x] 运行 Python 全量测试、compileall、wheel 构建与隔离导入；Swift build 在本机 CommandLineTools `emit-module` 阶段超时且无诊断。
- [x] 运行 CLI run、skills index、benchmark 冒烟。
- [x] 扫描兼容残留、清理构建产物并运行 `git diff --check`。
- [x] 排除 `.gitignore` 后提交 `refactor(core): 发布 v0.0.18 清理兼容薄壳`。
