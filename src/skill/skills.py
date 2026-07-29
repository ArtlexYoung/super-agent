"""Central owner for progressive Skill discovery, disclosure, and loading."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from core.checks import ActionRequest
from core.models import RunIdentity
from core.provider.chat import Message
from skill.disclosure import ProgressiveDisclosureCore, SkillReference
from skill.runners.loaded import LoadedSkill
from skill.runners.registry import SkillLoadRequest, SkillRunner, SkillRunners

if TYPE_CHECKING:
    from skill.state.store import RuntimeStore


@dataclass(frozen=True)
class SkillServices:
    """Optional services exposed explicitly while one Skill is loaded."""

    store: RuntimeStore | None = None
    identity: RunIdentity | None = None
    send_text_model_messages: Callable[[list[Message]], str] | None = None
    execute_action: Callable[[ActionRequest, Callable[[], object]], object] | None = None


class Skills:
    """Keep one verified Skill snapshot and all trusted loaders together."""

    def __init__(
        self,
        disclosure: ProgressiveDisclosureCore,
        loaders: SkillRunners | None = None,
    ) -> None:
        self.disclosure = disclosure
        self.loaders = loaders or SkillRunners()
        self.index = disclosure.prepare_skill_index()

    def add_loader(self, loader: SkillRunner, *, replace: bool = False) -> None:
        self.loaders.add_skill_runner(loader, replace=replace)

    def find_loader(self, skill_type: str) -> SkillRunner | None:
        return self.loaders.find_skill_runner(skill_type)

    def list_loaders(self) -> list:
        return self.loaders.list_skill_runners()

    def list_model_context_types(self) -> set[str]:
        return self.loaders.list_model_context_types()

    def validate_loaders(self) -> None:
        self.loaders.validate_dependencies()

    def open(self, reference: SkillReference):
        return self.disclosure.open_skill(reference.name, reference.skill_type)

    def load(
        self,
        reference: SkillReference,
        services: SkillServices | None = None,
    ) -> LoadedSkill:
        selected = services or SkillServices()
        return self.loaders.load_skill(
            SkillLoadRequest(
                self.disclosure,
                reference,
                selected.store,
                selected.identity,
                selected.send_text_model_messages,
                selected.execute_action,
            )
        )
