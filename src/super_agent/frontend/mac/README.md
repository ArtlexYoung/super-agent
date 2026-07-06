# Super Agent Mac

一个轻量 SwiftUI macOS 前端，用于管理对话、编辑 `agent.toml`、按 `[model]` 配置发送消息。

## 功能

- 最左侧导航：在“对话”和“配置”两个页面间切换。
- 对话页：管理多轮对话，新建、选择、重命名、删除、清空，并自动持久化到本机 Application Support。
- 配置页：用中文表单可视化编辑 `agent.toml`，覆盖 `[agent]`、`[model]`、`[paths]`，也能直接编辑 TOML 原文。
- 模型列表：模型页支持多个模型配置，模型名称、服务地址、密钥环境变量都可以输入，并自动保存到桌面端 `config.json`。
- 自动选项：打开配置后会扫描 `paths.skills` 和 `paths.mcp`，技能、skill 能力、MCP 分成独立列表，勾选就是打开。
- 默认行为：记忆和工作流有“默认”标签，并提供按钮快速切换默认行为。
- 配置提示：每个配置项都有 `？` 图标，鼠标悬浮即可查看详细说明。
- 模型对话：`mock` provider 返回本地回复；`openai-compatible/openai` 和 `anthropic-compatible/anthropic` 会按 TOML 配置请求模型。
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

```bash
cd src/super_agent/frontend/mac
swift run SuperAgentMac
```

## 打包

```bash
cd src/super_agent/frontend/mac
./package_release.sh
```

脚本会读取根目录 `pyproject.toml` 的版本号，生成：

```text
release/mac/SuperAgentMac.app
release/mac/SuperAgentMac-<version>-macOS.zip
```

本地未签名 app 如果被 Gatekeeper 拦截，可以在 Finder 里右键 app 后选择“打开”。
