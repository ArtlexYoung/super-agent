# 发布 / Releasing

## 本地检查 / Local Checks

使用 Python 3.11 和 `uv`：

Use Python 3.11 and `uv`:

```bash
PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 \
python3.11 -m unittest discover -s tests -p 'test_*.py' -v

PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 \
python3.11 scripts/verify_release.py --version 0.2.0 --full
```

发布检查验证版本、Python 最低版本、零默认依赖、源码布局、旧目录删除、构建范围、内置 Skill 和离线评测资产。

The release gate checks version, Python minimum, zero default dependencies, source layout, removed legacy directories, build scope, builtin Skills, and offline evaluation assets.

## 版本 / Version

v0.2.0 是破坏性重构版本。同步更新 `pyproject.toml`、`src/core/__init__.py`、README 和发布检查脚本。

v0.2.0 is a breaking rewrite. Update `pyproject.toml`, `src/core/__init__.py`, the READMEs, and the release gate together.

检查通过后创建一个本地逻辑提交和标签；远程推送是单独的授权边界。

After checks pass, create one local logical commit and tag; pushing to a remote is a separate authorization boundary.
