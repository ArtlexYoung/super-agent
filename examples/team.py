from __future__ import annotations

from core import Agent


def main() -> None:
    master = Agent.load_from_config_file("examples/basic/agent.toml")
    coder = Agent.load_from_config_file("examples/basic/agent.toml")
    reviewer = Agent.load_from_config_file("examples/basic/agent.toml")

    master.add_subagent(coder, name="coder", description="writes code", triggers=["code"])
    master.add_subagent(reviewer, description="reviews risks", triggers=["review"])

    result = master.run("please write code and review the result")
    print(result.text)
    for subagent_result in result.subagent_results or []:
        print(f"{subagent_result.name}: {subagent_result.text}")


if __name__ == "__main__":
    main()
