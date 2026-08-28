# CLI

## 直接运行 / Direct Use

CLI 入口是 `super-agent`，源码检出也可以运行 `python3.11 src/cli.py`。

The CLI entry point is `super-agent`; a source checkout may also run `python3.11 src/cli.py`.

```bash
super-agent "总结当前目录"
super-agent --plugin plugin:super-agent/code "检查这个仓库"
super-agent --skill skill:super-agent/review/multi-agent "检视当前方案"
super-agent --save "保存本次对话"
super-agent
```

`--plugin` 显式激活整个插件及其依赖，`--skill` 只激活一个规范 Skill 引用。两者只影响本次运行，不会写回配置。

`--plugin` explicitly activates a plugin and its dependencies, while `--skill` activates one canonical Skill reference. Both affect only the current run and never rewrite configuration.

没有参数时进入交互会话。`/help`、`/plugins`、`/skills`、`/mcps`、`/clear` 和 `/exit` 是终端控制命令，不会被发送给模型，也不是 Skill 触发词。

Without arguments the CLI starts an interactive session. `/help`, `/plugins`, `/skills`, `/mcps`, `/clear`, and `/exit` are terminal controls, are never sent to the model, and are not Skill trigger words.

## 目录命令 / Catalog Commands

```bash
super-agent plugins list --config common.toml
super-agent plugins read plugin:super-agent/common --config common.toml
super-agent skills list --config common.toml
super-agent skills read skill:super-agent/review/multi-agent --config common.toml
super-agent mcps list --config common.toml
super-agent mcps read mcp:example/search --config common.toml
```

`list` 只返回有界索引，`read` 只预览内容且不激活。禁用项不会出现在索引中。引用必须使用规范的 `plugin:`、`skill:` 或 `mcp:` 形式，不做旧名称猜测。

`list` returns only a bounded index, and `read` previews content without activation. Disabled entries stay out of the index. References must use canonical `plugin:`, `skill:`, or `mcp:` form; legacy names are never guessed.

## 检查与配置 / Check and Configuration

```bash
super-agent check --config common.toml
super-agent config show --config common.toml
super-agent config validate --config common.toml
```

`check` 验证通用配置、模型前置条件以及中央资源依赖图，并报告插件、Skill、MCP 和模型数量。它不创建存储、不调用模型、不安装插件。

`check` validates general configuration, model prerequisites, and the central resource dependency graph, then reports plugin, Skill, MCP, and model counts. It creates no storage, calls no model, and installs no plugin.

`config show` 分别显示 CLI 和通用配置；`config validate` 还校验明确引用的 `code.toml`。三份配置不深度合并。

`config show` displays CLI and general configuration separately; `config validate` also validates an explicitly referenced `code.toml`. The three files are never deep-merged.

## 数据命令 / Data Commands

```bash
super-agent data storage verify --config common.toml
super-agent data storage prune --config common.toml --user alice
super-agent data storage prune --config common.toml --user alice --apply
super-agent data conversations list --config common.toml --user alice
```

`data` 需要显式存储后端。`storage prune` 默认只预览到期详细和关键记录，只有 `--apply` 才删除；`--apply` 用于其他动作会直接失败。

`data` requires an explicit storage backend. `storage prune` previews expired detailed and critical records by default and deletes only with `--apply`; using `--apply` with another action fails.

## 配置查找 / Configuration Lookup

未指定 `--cli-config` 时，CLI 从当前目录向上寻找最近的 `cli.toml`，再检查用户配置目录。未指定 `--config` 时，从当前目录向上寻找最近的 `common.toml`。相对路径始终以所属配置文件目录解析。

Without `--cli-config`, the CLI searches upward for the nearest `cli.toml` and then checks the user configuration directory. Without `--config`, it searches upward for the nearest `common.toml`. Relative paths resolve from their owning configuration file.

没有 `code.toml` 时，当前目录是工作区，Git 读取可用，写入和删除为 `ask`，且没有预设验证命令。非交互环境不会自动确认副作用。

Without `code.toml`, the current directory is the workspace, Git reads are available, writes and deletes use `ask`, and no verification command is assumed. Non-interactive environments never auto-confirm effects.
