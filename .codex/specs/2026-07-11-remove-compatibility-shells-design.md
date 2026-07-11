# v0.0.18 兼容层与薄壳清理设计

## 目标

删除 `v0.0.17` 统一中心披露核心后遗留的兼容入口、动态导出 facade 和无业务语义的单行转换，让导入关系、配置名称和持久化格式只有一条明确路径。

## 保留边界

- `super_agent.py` 是唯一公共 Python API 聚合入口。
- `core.*`、`skill.*` 内部代码直接导入定义模块，不经过包级转发。
- schema 序列化函数保留，因为它们负责稳定外部格式与版本校验。
- `SkillDisclosure` 到 MCP、memory、workflow 的构造函数保留，因为它们负责 kind 校验和中心配置读取。

## 删除内容

- 删除 `core`、`skill`、`skill.ecosystem`、`skill.evolution`、`skill.kinds` 的动态 `__getattr__` 导出表。
- 删除 provider 别名 `openai` 和 `anthropic`，只接受 `openai-compatible` 与 `anthropic-compatible`。
- 删除 macOS 对旧单文件 `state.json` 的读取，只读取当前 `index.json`、会话 JSON 和 `config.json`。
- 删除 `BenchmarkReport.to_dict()` 对 `benchmark_report_to_dict()` 的单行转发。
- 删除 Swift `SkillIndexEntry.makeChoice()` 字段搬运方法，并要求中心索引 DTO 提供完整键和分组。

## 错误行为

未知 provider 在读取密钥前立即报告 `unknown provider`。中间包不再导出业务对象，内部调用必须指向真实模块；外部用户统一从 `super_agent` 导入。

## 验收

- 源码不存在 `_EXPORTS`、包级 `__getattr__`、legacy macOS 状态类型或 provider 别名。
- 项目内部不存在 `from core import ...` 或 `from skill import ...`。
- Python 全量测试、compileall、Swift build、wheel 隔离导入和 CLI 冒烟通过。
