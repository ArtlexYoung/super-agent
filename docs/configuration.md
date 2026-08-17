# 配置 / Configuration

配置按责任拆分，不把 CLI、通用运行时和代码工作区混在一个表里。

Configuration is split by ownership; CLI, general runtime, and coding workspace settings are not merged into one table.

## 通用配置 / General Configuration

`common.toml` 只描述 Agent 组合：

`common.toml` describes only Agent composition:

```toml
version = 1
name = "my-agent"
instructions = ["回答要简洁，并说明不确定性。"]
skill_paths = ["skills"]
enabled_skills = ["task:common"]
disabled_skills = []
memory = false
evolution = false
warn_agent_level = 8
# max_agent_level = 6
# max_agent_call_depth = 12

[[models]]
name = "default"
provider = "openai-compatible"
model = "THUDM/GLM-4-9B-0414"
base_url = "https://api.siliconflow.cn/v1"
api_key_env = "OA3_SILICONFLOW_API_KEY"
weight = 1.0
purposes = ["auto", "code"]
features = ["text", "tools"]

[storage]
backend = "jsonl"
path = ".super-agent/data"
detailed_log_days = 180
critical_log_days = 365
```

未知字段直接报错。`storage.backend` 可选 `none`、`memory`、`jsonl`、`sqlite`、`mysql` 或 `postgresql`；没有显式后端时 Agent 保持无状态。

Unknown fields fail explicitly. `storage.backend` accepts `none`, `memory`, `jsonl`, `sqlite`, `mysql`, or `postgresql`; without an explicit backend the Agent remains stateless.

`warn_agent_level` 只产生提醒。`max_agent_level` 限制代码中组织树的层级，`max_agent_call_depth` 限制递归委派的实际调用深度；两个最大值省略时均为无限。组和子 Agent 本身只在 Python 代码中组合，不写入 TOML。

`warn_agent_level` only emits warnings. `max_agent_level` limits the code-defined organization tree, and `max_agent_call_depth` limits actual recursive delegation; omitting either maximum means unlimited. Groups and subagents are composed only in Python, not TOML.

## CLI 与代码配置 / CLI and Code Configuration

`cli.toml` 只管理终端输出、用户和保存行为；`code.toml` 只管理工作区、写入策略和预声明验证命令。三类配置分别加载、分别校验，不进行深度合并。

`cli.toml` controls terminal output, user, and saving; `code.toml` controls workspace, write policy, and declared checks. The three scopes load and validate independently, with no deep merge.

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

`deny` 不注册对应工具，`ask` 在终端逐次确认，`allow` 表示用户已在代码配置中明确授权。验证命令必须与声明的完整参数数组一致。`cli.toml` 中的相对 `general_config` 和 `code_config` 路径以该 CLI 文件所在目录解析。

`deny` omits the tool, `ask` confirms each terminal call, and `allow` records explicit permission in code configuration. Verification commands must match a complete declared argument array. Relative `general_config` and `code_config` paths in `cli.toml` resolve from that CLI file's directory.

没有 `code.toml` 时，当前目录作为工作区，Git 读取直接可用，写入和删除默认为 `ask`，不配置验证命令。非交互环境不会自动同意确认；需要自动修改时必须显式配置 `allow`。

Without `code.toml`, the current directory is the workspace, Git reads are available, writes and deletes default to `ask`, and no check commands are assumed. Non-interactive runs never auto-confirm; automation must explicitly configure `allow`.

配置加载是读取操作。创建 JSONL、连接数据库、写 Skill 或执行进程都必须由后续显式动作触发。

Loading configuration is a read. Creating JSONL, connecting to a database, writing a Skill, or starting a process requires a later explicit action.

## 环境变量 / Environment Variables

- `OA3_SILICONFLOW_API_KEY`：自动选择文档中的 SiliconFlow 示例模型。
- `SUPER_AGENT_PROVIDER=mock`：显式离线模型。
- `SUPER_AGENT_MODEL`、`SUPER_AGENT_BASE_URL`、`SUPER_AGENT_API_KEY_ENV`：通用环境模型设置。
- `SUPER_AGENT_CLI_CONFIG`：指定 CLI 配置文件。

- `OA3_SILICONFLOW_API_KEY`: selects the documented SiliconFlow example model.
- `SUPER_AGENT_PROVIDER=mock`: explicitly selects the offline model.
- `SUPER_AGENT_MODEL`, `SUPER_AGENT_BASE_URL`, `SUPER_AGENT_API_KEY_ENV`: generic environment model settings.
- `SUPER_AGENT_CLI_CONFIG`: selects a CLI configuration file.
