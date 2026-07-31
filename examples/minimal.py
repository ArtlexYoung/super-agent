"""Run one stateless Agent without configuration or network access."""

from core.provider.chat import MockProvider
from super_agent import Agent


agent = Agent(provider=MockProvider("Hello from a minimal Agent."))
print(agent.run("Say hello").text)
