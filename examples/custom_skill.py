"""使用项目自有的任务 Skill 运行一个 Agent。"""

from pathlib import Path

from core.provider import MockProvider
from super_agent import Agent


config = Path(__file__).with_name("custom") / "common.toml"
agent = Agent(config, provider=MockProvider("The custom Skill was selected."))
result = agent.run("Use the custom task", skill="custom")
print(result.text)
print(", ".join(result.skills))
