from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "LockedSkill": ("skill.ecosystem.lock", "LockedSkill"),
    "SkillPackageManager": ("skill.ecosystem.package", "SkillPackageManager"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'skill.ecosystem' has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
