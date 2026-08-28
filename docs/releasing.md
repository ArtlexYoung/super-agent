# 发布 / Releasing

## 本地检查 / Local Checks

使用 Python 3.11 和 `uv`：

Use Python 3.11 and `uv`:

```bash
PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 \
python3.11 -m unittest discover -s tests -p 'test_*.py' -v

PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 \
python3.11 scripts/verify_release.py --version 0.2.23 --full
```

发布检查验证版本、Python 最低版本、零默认依赖、源码布局、旧目录删除、构建范围、内置插件去重关系和离线评测资产。

The release gate checks version, Python minimum, zero default dependencies, source layout, removed legacy directories, build scope, builtin plugin deduplication, and offline evaluation assets.

日志写入不会触发保留清理。先运行 `super-agent data storage prune --config common.toml --user alice` 预览，确认后追加 `--apply` 才执行删除；状态流不会因为期限被删除。

Log writes never trigger retention cleanup. Run `super-agent data storage prune --config common.toml --user alice` to preview, then add `--apply` to delete; state streams are not expired by this policy.

## 版本 / Version

v0.2.23 引入中央 Skill/MCP/插件资源库、规范引用、跨来源内容去重、哈希覆盖层、运行快照和逐插件/Skill 进化授权。同步更新 `pyproject.toml`、`src/core/__init__.py`、README 和发布检查脚本。

v0.2.23 introduces the central Skill/MCP/plugin library, canonical references, cross-source content deduplication, hash-based overlays, run snapshots, and per-plugin or per-Skill evolution authority. Update `pyproject.toml`, `src/core/__init__.py`, the READMEs, and the release gate together.

检查通过后创建一个本地逻辑提交和标签；远程推送是单独的授权边界。

After checks pass, create one local logical commit and tag; pushing to a remote is a separate authorization boundary.
