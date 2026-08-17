"""在代码中组合组、Agent 和已有子树。"""

from core.provider import MockModel
from skill.organization import AgentMemberSettings
from super_agent import Agent

master = Agent(MockModel("The team is ready."), name="master")
coder = Agent(MockModel("Implemented and checked the change."), name="coder")
reviewer = Agent(MockModel("Reviewed the change and found no blocker."), name="reviewer")

engineering = master.add_group("engineering", description="implements changes")
quality = master.add_group("quality", description="checks evidence and risks")
engineering.add_subagent(
    coder,
    name="coder",
    description="writes and verifies code",
    settings=AgentMemberSettings(purpose="code"),
)
quality.add_subagent(
    reviewer,
    name="reviewer",
    description="reviews risks",
    settings=AgentMemberSettings(purpose="review"),
)
print(master.list_agent_tree())
print(master.run("Prepare the team").text)
