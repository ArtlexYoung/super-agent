# CLI

## 直接运行 / Direct Use

CLI 入口是 `super-agent`，也可以从源码运行 `python3.11 src/cli.py`。

The CLI entry point is `super-agent`; source checkout users may run `python3.11 src/cli.py`.

```bash
super-agent "总结当前目录"
super-agent --output json "返回结构化结果"
super-agent --skill code --save "检查并修复代码"
super-agent
```

没有参数进入交互会话；`/help`、`/skills`、`/clear` 和 `/exit` 是终端控制命令。它们不是模型触发词，不会被送入模型。

Without arguments the CLI starts an interactive session; `/help`, `/skills`, `/clear`, and `/exit` are terminal controls. They are not model trigger words and are not sent to the model.

## 配置命令 / Configuration Commands

```bash
super-agent check --config common.toml
super-agent config show --config common.toml
super-agent config validate --config common.toml
super-agent skills list --config common.toml
super-agent skills read prompt:research --config common.toml
super-agent data storage verify --config common.toml
super-agent data conversations list --config common.toml --user alice
super-agent serve --config common.toml --cli-config cli.toml
```

这些命令只在其明确职责范围内工作。`check` 和 `config` 不创建存储；`data` 需要配置中显式启用后端；`serve` 才会启动 HTTP 服务。

Each command stays within its declared scope. `check` and `config` do not create storage; `data` requires an explicitly configured backend; only `serve` starts an HTTP server.

## 三份配置 / Three Config Files

- `common.toml`：模型、Skill、记忆、进化、存储和运行限制。
- `cli.toml`：输出格式、用户、保存开关、服务地址。
- `code.toml`：工作区路径、写入/删除/Git/执行策略、验证命令。

- `common.toml`: models, Skills, memory, evolution, storage, and run limits.
- `cli.toml`: output format, user, saving, and server address.
- `code.toml`: workspace path, write/delete/Git/execute policy, and declared checks.

配置文件不互相深度合并；同名或未知字段不会被静默覆盖。

Configuration files are not deep-merged; duplicate or unknown fields are never silently overridden.

代码动作使用 `deny`、`ask` 或 `allow`。拼写错误和未知字段直接失败；`allow` 不会再次询问，`ask` 才会逐次确认。模型可先调用 `list_process_commands` 查看完整允许列表，再提交完全一致的参数数组。

Code actions use `deny`, `ask`, or `allow`. Misspellings and unknown fields fail; `allow` does not prompt again, while `ask` confirms each call. The model can call `list_process_commands` before submitting an exact declared argument array.
