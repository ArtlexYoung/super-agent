from __future__ import annotations

from core.provider.chat import MockProvider
from super_agent import Agent


def main() -> None:
    master = Agent(provider=MockProvider("The team is ready."))
    coder = Agent(provider=MockProvider("Implemented and checked the change."))
    reviewer = Agent(provider=MockProvider("Reviewed the change and found no blocker."))

    master.add_subagent(coder, name="coder", description="writes code")
    master.add_subagent(reviewer, description="reviews risks")

    print(master.run("Prepare the team").text)
    print(coder.run("Implement the change", skill="code").text)
    print(reviewer.run("Review the change", skill="common").text)


if __name__ == "__main__":
    main()
