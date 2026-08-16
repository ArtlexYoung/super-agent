# 安全与副作用 / Safety and Side Effects

## 默认原则 / Defaults

读取尽量无副作用；写入、删除、进程启动、数据库连接和 Skill 覆盖都必须由明确代码路径触发。

Reads are intended to be side-effect free; writes, deletion, process starts, database connections, and Skill overlays require explicit code paths.

`preview` 是纯读取。显式配置磁盘披露缓存后，`disclose` 可能原子写入有界、可丢弃的缓存文件；它不会修改会话、记忆、Skill 或工作区状态。

`preview` is a pure read. With an explicit disk disclosure cache, `disclose` may atomically write a bounded, disposable cache file; it does not mutate conversation, memory, Skill, or workspace state.

Skill 是数据，不是可执行 Python。工具的效果通过 `Tool.effects` 声明，终端适配器可以对写入或进程动作逐次询问确认。

A Skill is data, not executable Python. Tool effects are declared through `Tool.effects`, and the terminal adapter can ask for confirmation for each write or process action.

## 工作区保护 / Workspace Protection

代码工具限制在配置的工作区内，拒绝路径逃逸、忽略路径、超大文件和非文本读取。替换或删除必须携带此前读取到的 SHA-256；文件被并发修改时操作失败，不覆盖新内容。

Code tools stay inside the configured workspace and reject path escapes, ignored paths, oversized files, and non-text reads. Replace and delete require a SHA-256 from a prior read; concurrent changes fail instead of overwriting new content.

进程工具只接受预声明的参数数组，不接受 shell 字符串。验证失败会作为事实返回给模型，不会自动修改文件或伪造通过。

Process tools accept declared argument arrays, never shell strings. A failed check is returned as evidence; files are not auto-modified and success is not fabricated.

## 审计与脱敏 / Audit and Redaction

记录后端保存完整事实，展示层默认对 prompt、模型正文、工具参数和错误进行动态脱敏。详细日志默认保留 180 天，关键日志默认保留 365 天，期限可在通用配置中调整。

Backends retain canonical facts, while views dynamically redact prompts, model text, tool payloads, and errors by default. Detailed logs live 180 days and critical logs 365 days by default; both are configurable.

动态脱敏不是加密。部署时仍需限制 JSONL 目录、数据库账号和缓存目录的访问。

Dynamic redaction is not encryption. Deployments must still restrict access to JSONL directories, database credentials, and cache directories.
