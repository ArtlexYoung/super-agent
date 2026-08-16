# Web

## 可选页面 / Optional UI

Web 不是核心依赖。`super-agent serve` 启动标准库 HTTP 服务，静态 React 页面位于 `src/adapter/static`，没有运行时 Node 依赖。

Web is not a core dependency. `super-agent serve` starts a standard-library HTTP server; the static React page is under `src/adapter/static`, with no Node runtime dependency.

```bash
super-agent serve --config common.toml --cli-config cli.toml
open http://127.0.0.1:8765/
```

页面左侧管理会话和配置，模型、Skill、记忆和子 Agent 树通过轻量 API 读取。没有显式存储时，bootstrap 仍可读取配置和 Skill，但会话、运行和记忆列表为空。

The page manages conversations and configuration from the left navigation. Models, Skills, memory, and the subagent tree come from the lightweight API. Without explicit storage, bootstrap still exposes configuration and Skills, while conversation, run, and memory lists stay empty.

配置和模型修改只写入 `--config` 明确指定的通用配置文件；没有文件支持时页面仍可对话和查看状态，但保存操作会明确失败。

Configuration and model changes write only to the general configuration file selected by `--config`; without a file-backed configuration the page can still chat and inspect state, but save operations fail explicitly.

## API 边界 / API Boundary

- `GET /api/bootstrap`：读取当前用户的配置、模型、Skill 和状态概览。
- `/api/conversations`：在启用存储后创建、读取、重命名、清空和删除会话。
- `GET /api/runs/<run_id>`：读取默认脱敏的运行解释。
- `/api/models` 和 `PUT /api/config`：在命名通用配置存在时显式更新配置。
- `POST /ag-ui`：把一次请求映射到同一条 Runtime 事件流。

- `GET /api/bootstrap`: reads configuration, models, Skills, and state overview for the user.
- `/api/conversations`: creates, reads, renames, clears, and deletes conversations when storage is enabled.
- `GET /api/runs/<run_id>`: reads a redacted run explanation.
- `/api/models` and `PUT /api/config`: explicitly update a named general configuration.
- `POST /ag-ui`: maps one request to the same Runtime event stream.

配置写入是显式 API 动作，Web 不会因为打开页面而自动保存默认配置。

Configuration writes are explicit API actions; opening the page does not save defaults automatically.

## CopilotKit 示例 / CopilotKit Example

`web/src/components/copilotkit/copilotkit-example-page.tsx` 只展示如何将 CopilotKit 和 `@ag-ui/client` 接到 `/ag-ui`，不创建第二套 Agent 循环。替换模型或 UI 时，Provider 和 Runtime 无需改变。

`web/src/components/copilotkit/copilotkit-example-page.tsx` only demonstrates connecting CopilotKit and `@ag-ui/client` to `/ag-ui`; it does not create a second Agent loop. Provider and Runtime remain unchanged when the UI or model changes.
