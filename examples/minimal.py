"""无需配置或网络，运行一个无状态 Agent。"""

from core.provider import MockProvider
from super_agent import Agent


agent = Agent(provider=MockProvider("Hello from a minimal Agent."))
print(agent.run("Say hello").text)
