# 发布 / Releasing

## 本地检查 / Local Checks

使用 Python 3.11 和 `uv`：

Use Python 3.11 and `uv`:

```bash
PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 \
python3.11 -m unittest discover -s tests -p 'test_*.py' -v

PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 \
python3.11 scripts/verify_release.py --version 0.2.15 --full
```

发布检查验证版本、Python 最低版本、零默认依赖、源码布局、旧目录删除、构建范围、内置 Skill 和离线评测资产。

The release gate checks version, Python minimum, zero default dependencies, source layout, removed legacy directories, build scope, builtin Skills, and offline evaluation assets.

日志写入不会触发保留清理。先运行 `super-agent data storage prune --config common.toml --user alice` 预览，确认后追加 `--apply` 才执行删除；状态流不会因为期限被删除。

Log writes never trigger retention cleanup. Run `super-agent data storage prune --config common.toml --user alice` to preview, then add `--apply` to delete; state streams are not expired by this policy.

## 版本 / Version

v0.2.15 收敛显式检查点恢复、工作目录记忆隔离和日志清理边界。同步更新 `pyproject.toml`、`src/core/__init__.py`、README 和发布检查脚本。

v0.2.15 finalizes explicit checkpoint recovery, working-directory memory isolation, and retention boundaries. Update `pyproject.toml`, `src/core/__init__.py`, the READMEs, and the release gate together.

检查通过后创建一个本地逻辑提交和标签；远程推送是单独的授权边界。

After checks pass, create one local logical commit and tag; pushing to a remote is a separate authorization boundary.
