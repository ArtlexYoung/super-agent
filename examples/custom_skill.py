"""从独立 TOML 配置加载一个本地 Skill。"""

from pathlib import Path

from core.config import Config
from super_agent import Agent


config = Config.load(Path(__file__).with_name("custom") / "common.toml")
agent = Agent(config=config)
result = agent.run("Use the custom task")
print(result.text)
print(", ".join(result.skills))
