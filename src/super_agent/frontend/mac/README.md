# Super Agent Mac

一个轻量 SwiftUI macOS 对话页面原型，包含三块：

- 左侧：对话管理，新建、选择、删除、清空消息。
- 中间：对话消息、输入框、本地模拟回复。
- 右侧：配置服务地址、模型、API Key、温度、系统提示词。

运行：

```bash
cd src/super_agent/frontend/mac
swift run SuperAgentMac
```

当前没有直接请求后端。`ChatStore.sendDraftMessage()` 先返回本地模拟回复，后续可以替换为调用 Python runtime、HTTP 服务或本地进程。
