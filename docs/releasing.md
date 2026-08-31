# 发布 / Releasing

## 本地检查 / Local Checks

使用 Python 3.11 和 `uv`：

Use Python 3.11 and `uv`:

```bash
PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 \
python3.11 -m unittest discover -s tests -p 'test_*.py' -v

PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 \
python3.11 scripts/verify_release.py --version 0.2.67 --full
```

发布检查验证版本、Python 最低版本、零默认依赖、源码布局、旧目录删除、构建范围、内置插件去重关系和离线评测资产。

The release gate checks version, Python minimum, zero default dependencies, source layout, removed legacy directories, build scope, builtin plugin deduplication, and offline evaluation assets.

日志写入不会触发保留清理。先运行 `super-agent data storage prune --config common.toml --user alice` 预览，确认后追加 `--apply` 才执行删除；状态流不会因为期限被删除。

Log writes never trigger retention cleanup. Run `super-agent data storage prune --config common.toml --user alice` to preview, then add `--apply` to delete; state streams are not expired by this policy.

## 版本 / Version

v0.2.67 增加独立 Agent 结果收集与多样性校验，支撑旁观复核和交叉验证。

v0.2.67 adds independent-Agent result collection and diversity checks for external review and cross-validation.

检查通过后创建一个本地逻辑提交和标签；远程推送是单独的授权边界。

After checks pass, create one local logical commit and tag; pushing to a remote is a separate authorization boundary.
