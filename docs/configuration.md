# 配置 / Configuration

配置按责任拆分：`common.toml` 组合通用 Agent，`cli.toml` 只控制终端，`code.toml` 只控制代码工作区和动作权限。

Configuration is split by ownership: `common.toml` composes a general Agent, `cli.toml` controls only the terminal, and `code.toml` controls only coding workspace and action policy.

## 通用配置 / General Configuration

```toml
version = 1
name = "my-agent"
instructions = ["回答要简洁，并说明不确定性。"]

plugin_paths = ["plugins"]
writable_plugin_path = ".super-agent/plugins"
plugin_cache_path = ".super-agent/cache"
enabled_plugins = ["plugin:super-agent/common"]
disabled_plugins = []
enabled_skills = ["skill:super-agent/common/review"]
disabled_skills = []

memory = false
warn_agent_level = 8
# max_agent_level = 6
# max_agent_call_depth = 12

[evolution]
allow = ["plugin:local/research", "skill:super-agent/common/review"]
auto_apply = []

[[models]]
name = "default"
provider = "openai-compatible"
model = "your-model"
description = "擅长复杂代码修改、调试和长上下文仓库分析"
base_url = "https://provider.example/v1"
api_key_env = "MODEL_API_KEY"
weight = 1.0
purposes = ["auto", "code"]
features = ["text", "tools"]

[storage]
backend = "jsonl"
path = ".super-agent/data"
detailed_log_days = 180
critical_log_days = 365
```

未知字段直接失败。读取配置不创建目录、不连接数据库、不写插件，也不启动进程；这些副作用只由后续显式动作触发。

Unknown fields fail directly. Loading configuration creates no directory, opens no database, writes no plugin, and starts no process; later explicit actions own those effects.

`plugin_paths` 指向插件根的父目录。每个子目录至少包含标准 `SKILL.md`；使用 Super Agent 插件身份、依赖和版本时再添加 `plugin.toml`。标准外部 Skill 不要求修改格式，也不会被复制成另一份内容。

`plugin_paths` points to parent directories of plugin roots. Every child needs at least a standard `SKILL.md`; add `plugin.toml` for Super Agent identity, dependencies, and versioning. A standard external Skill requires no format rewrite and is not copied into another content form.

`enabled_plugins` 激活插件入口及依赖，`enabled_skills` 只激活指定成员。禁用项优先，且只影响之后建立的运行快照。

`enabled_plugins` activates plugin entries and dependencies, while `enabled_skills` activates only named members. Disabled entries take precedence and affect only later run snapshots.

`[evolution]` 是唯一配置授权源。`allow` 允许模型提出和测试指定插件或 Skill 的更新；`auto_apply` 必须是其子集，且只允许测试通过后的自动应用。Skill 文档中的文本和元数据不能扩大权限。

`[evolution]` is the only configuration authority. `allow` permits model-proposed and tested updates for selected plugins or Skills; `auto_apply` must be its subset and applies only after tests pass. Skill text and metadata cannot expand authority.

`storage.backend` 可选 `none`、`memory`、`jsonl`、`sqlite`、`mysql` 或 `postgresql`。没有显式后端时 Agent 保持无状态；MySQL 和 PostgreSQL 驱动仍是可选依赖。

`storage.backend` accepts `none`, `memory`, `jsonl`, `sqlite`, `mysql`, or `postgresql`. Without an explicit backend the Agent remains stateless; MySQL and PostgreSQL drivers remain optional dependencies.

`models.description` 是用户给出的只读先验。可靠性、用量、延迟和显式质量评价按用户、Agent 与 `purpose` 独立学习，不覆盖原描述。

`models.description` is a read-only user prior. Reliability, usage, latency, and explicit quality evaluations learn separately by user, Agent, and `purpose` without overwriting it.

组和子 Agent 只在 Python 代码中组合。每个 Agent 可以加载不同插件；`warn_agent_level` 只提醒，两个最大值省略时均为无限。

Groups and subagents compose only in Python. Each Agent may load different plugins; `warn_agent_level` only warns, and both omitted maxima are unlimited.

## CLI 配置 / CLI Configuration

```toml
version = 1
general_config = "common.toml"
code_config = "code.toml"
user_id = "local"
output = "text"
save = false
show_summary = true
```

`cli.toml` 不与通用配置深度合并。相对 `general_config` 和 `code_config` 路径以 `cli.toml` 所在目录解析。

`cli.toml` is not deep-merged with general configuration. Relative `general_config` and `code_config` paths resolve from the directory containing `cli.toml`.

## 代码配置 / Code Configuration

```toml
version = 1

[workspace]
root = "."
ignore = [".git", ".super-agent", "node_modules", ".venv"]

[actions]
write = "ask"
delete = "deny"
git = "allow"
execute = "ask"

[verification]
commands = [["python3.11", "-m", "unittest"]]
timeout_seconds = 120
max_output_bytes = 1000000
max_processes = 4
```

`deny` 不注册动作，`ask` 在终端逐次确认，`allow` 表示用户已在代码配置中授权。验证命令必须与声明的完整参数数组一致；非交互环境不会自动同意确认。

`deny` omits an action, `ask` confirms each terminal call, and `allow` records user authority in code configuration. Verification commands must match a complete declared argument array; non-interactive runs never auto-confirm.

## 环境变量 / Environment Variables

- `SUPER_AGENT_PROVIDER=mock`：显式离线模型。
- `SUPER_AGENT_MODEL`、`SUPER_AGENT_BASE_URL`、`SUPER_AGENT_API_KEY_ENV`：通用环境模型设置。
- `SUPER_AGENT_CLI_CONFIG`：指定 CLI 配置文件。

- `SUPER_AGENT_PROVIDER=mock`: explicitly selects the offline model.
- `SUPER_AGENT_MODEL`, `SUPER_AGENT_BASE_URL`, and `SUPER_AGENT_API_KEY_ENV`: generic model settings.
- `SUPER_AGENT_CLI_CONFIG`: selects a CLI configuration file.

`api_key_env` 只保存环境变量名。密钥值不得写入 TOML、源码或文档。

`api_key_env` stores only an environment-variable name. Secret values must not be written to TOML, source, or documentation.
