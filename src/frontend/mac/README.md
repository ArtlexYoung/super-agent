# Super Agent Mac

一个轻量 SwiftUI macOS 客户端，直接使用同一套 Super Agent Python Runtime 管理对话、Skill 配置、模型和运行洞察。

## 功能

- 最左侧导航在“对话”和“配置”页面之间切换。
- 对话由 Runtime 存储，支持新建、选择、重命名、删除、清空和多轮历史。
- 主 Agent 与子 Agent 以任务树展示，点击任意节点可查看该节点的输入与输出。
- 运行洞察展示调度原因、模型调用状态、延迟、token、成本、路由证据、Skill 保鲜度和自动进化决策。
- 配置页使用中文表单编辑 `[agent]`、`[paths]` 和 `[storage]`，每个选项都有悬浮 `？` 说明。
- prompt、MCP、memory 和 workflow 都来自中心 Skill 索引；勾选表示启用，memory 与 workflow 可直接切换默认行为。
- 模型页直接创建和更新真实 `model` Skill，而不是维护桌面端私有模型格式。
- 模型名称、服务地址、密钥变量名和真实 API Key 可输入；支持能力与任务用途使用勾选项，质量、延迟和成本使用滑块或步进器。
- 真实 API Key 只保存在 macOS 钥匙串。`agent.toml`、`skill.toml`、`config.json`、运行事件和运行锁都不会包含密钥值。
- JSONL 是默认零依赖存储，也可选择标准库 SQLite，或按需安装 MySQL/PostgreSQL 驱动。

桌面端不实现第二套 Skill 解析、证据计算或会话存储。它统一调用：

```text
super-agent run
super-agent conversations ...
super-agent skills index
super-agent models list/save/remove
super-agent runs explain
```

## 本地状态

Runtime 选择的存储后端是会话、运行、评价、记忆和进化状态的唯一事实来源。桌面端 Application Support 的 `config.json` 只保存当前会话选择、可视化 Agent TOML 设置和已绑定配置路径。

没有绑定 `agent.toml` 时，相对 Skill 与存储路径会解析到 Application Support 下的默认项目目录。模型元数据保存在该项目的标准 `skills/model/<name>/skill.toml` 中；密钥保存在 Keychain 中。

## 运行

先安装仓库根目录项目，确保 `super-agent` 命令可用：

```bash
python3 -m pip install -e ../../..
swift run
```

需要使用其他入口时，可以设置命令名或绝对路径：

```bash
SUPER_AGENT_COMMAND=/path/to/super-agent swift run
```

## 构建与打包

```bash
swift build
./package_release.sh
```

打包脚本读取根目录 `pyproject.toml` 的版本号，生成：

```text
release/mac/SuperAgentMac.app
release/mac/SuperAgentMac-<version>-macOS.zip
```

本地包未签名。若 Gatekeeper 拦截，可在 Finder 中右键应用后选择“打开”。
