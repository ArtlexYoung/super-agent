"""最小的离线运行示例。"""

from core.provider import MockModel
from super_agent import Agent


agent = Agent(MockModel("Hello from a minimal Agent."))
print(agent.run("Say hello").text)
