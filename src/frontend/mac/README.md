# Super Agent Mac

一个轻量 SwiftUI macOS 前端，用于管理对话、编辑 `agent.toml`，并通过 Super Agent Python runtime 运行对话。

## 功能

- 最左侧导航：在“对话”和“配置”两个页面间切换。
- 对话页：管理多轮对话，新建、选择、重命名、删除、清空，并自动持久化到本机 Application Support。
- 配置页：用中文表单可视化编辑 `agent.toml`，覆盖 `[agent]`、`[model]`、`[paths]`，也能直接编辑 TOML 原文。
- 模型列表：模型页支持多个模型配置，模型名称、服务地址、密钥环境变量都可以输入，并自动保存到桌面端 `config.json`。
- 自动选项：打开配置后会递归扫描 `paths.skills`，普通技能和 `kind = "mcp"` 的 MCP 会分成独立列表，勾选就是打开。
- 技能保鲜度：技能列表会读取 `skill.toml` 和 `.super-agent/memory/skill_stats.json`，展示保鲜度、分组、调用次数、成功次数和替代次数。
- 默认行为：记忆和工作流有“默认”标签，并提供按钮快速切换默认行为。
- 配置提示：每个配置项都有 `？` 图标，鼠标悬浮即可查看详细说明。
- 统一运行时：桌面端通过 JSONL 协议调用 `super-agent run`，provider、Skill、workflow 和子 agent 都只由 Python runtime 执行。
- 真实运行树：主 agent 与子 agent 节点保存 runtime 返回的 `run_id`，可沿树查看每个节点的输入和输出。
- 会话存储：桌面端会把每个会话按会话 ID 保存成独立 JSON，并在最外层维护 `index.json`。
- 模型配置统一来自 TOML：`provider`、`model`、`base_url`、`api_key_env` 都写在 `[model]`。

示例模型配置：

```toml
[model]
provider = "openai-compatible"
model = "gpt-4.1-mini"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
```

## 运行

先在当前 Python 环境安装根目录项目，确保 `super-agent` 命令可用：

```bash
python3 -m pip install -e ../../..
```

```bash
cd src/frontend/mac
swift run SuperAgentMac
```

需要使用其他入口时，可以在启动前设置命令名或绝对路径：

```bash
SUPER_AGENT_COMMAND=/path/to/super-agent swift run SuperAgentMac
```

## 打包

```bash
cd src/frontend/mac
./package_release.sh
```

脚本会读取根目录 `pyproject.toml` 的版本号，生成：

```text
release/mac/SuperAgentMac.app
release/mac/SuperAgentMac-<version>-macOS.zip
```

本地未签名 app 如果被 Gatekeeper 拦截，可以在 Finder 里右键 app 后选择“打开”。
