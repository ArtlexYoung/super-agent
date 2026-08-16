"""在代码中自然组合主 Agent 和子 Agent。"""

from core.provider import MockModel
from super_agent import Agent


master = Agent(MockModel("The team is ready."), name="master")
coder = Agent(MockModel("Implemented and checked the change."), name="coder")
reviewer = Agent(MockModel("Reviewed the change and found no blocker."), name="reviewer")

master.add_subagent(coder, name="coder", description="writes and verifies code", purpose="code")
master.add_subagent(reviewer, name="reviewer", description="reviews risks", purpose="review")
print(master.run("Prepare the team").text)
