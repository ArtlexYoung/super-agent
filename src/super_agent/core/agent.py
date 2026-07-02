from __future__ import annotations

from super_agent.core.config import AgentConfig
from super_agent.core.provider import ChatProvider, build_provider
from super_agent.memory import MiniMemory
from super_agent.skill import ProgressiveDisclosure, SkillLoader
from super_agent.workflow import RunResult, get_workflow


class Agent:
    def __init__(
        self,
        config: AgentConfig,
        *,
        provider: ChatProvider | None = None,
        skill_loader: SkillLoader | None = None,
    ) -> None:
        self.config = config
        self.provider = provider or build_provider(config.model)
        self.skill_loader = skill_loader or SkillLoader(config.paths.skills)

    def run(self, prompt: str) -> RunResult:
        memory = MiniMemory(self.config.paths.memory)
        disclosure = ProgressiveDisclosure(self.skill_loader, self.config.paths.memory / "disclosure")
        disclosure_bundle = disclosure.prepare(prompt, self.config.agent.skills)
        workflow = get_workflow(self.config.agent.workflow)
        system = _system_with_memory(self.config.agent.system, memory)
        system = _system_with_disclosure(system, disclosure_bundle.as_instruction())
        result = workflow.run(
            prompt=prompt,
            system=system,
            model=self.config.model.model,
            skills=disclosure_bundle.skills,
            provider=self.provider,
        )
        memory.record_usage(result.workflow, result.skills)
        return result


def _system_with_memory(system: str, memory: MiniMemory) -> str:
    instruction = memory.as_instruction()
    return f"{system}\n\n{instruction}" if instruction else system


def _system_with_disclosure(system: str, disclosure: str) -> str:
    return f"{system}\n\n{disclosure}" if disclosure else system
