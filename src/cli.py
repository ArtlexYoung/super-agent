"""源码树的 CLI 直接入口，转发到 super_agent.adapter.cli 的唯一实现。"""

from super_agent.adapter.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
