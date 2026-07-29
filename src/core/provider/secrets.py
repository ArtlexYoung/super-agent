"""Resolve provider environment values without storing or exposing secrets."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping

from core.models import validate_user_id


UserSecretLookup = Callable[[str, str], str | None]


class UserSecretResolver:
    """Create a non-enumerable environment view for one validated user."""

    def __init__(
        self,
        lookup: UserSecretLookup | None = None,
        process_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.lookup = lookup
        self.process_environment = (
            os.environ if process_environment is None else process_environment
        )

    def get_environment_for_user(self, user_id: str) -> Mapping[str, str]:
        clean_user_id = validate_user_id(user_id)
        if self.lookup is None:
            return self.process_environment
        return _UserSecretEnvironment(clean_user_id, self.lookup)


class _UserSecretEnvironment(Mapping[str, str]):
    def __init__(self, user_id: str, lookup: UserSecretLookup) -> None:
        self.user_id = user_id
        self.lookup = lookup

    def __getitem__(self, name: str) -> str:
        if not isinstance(name, str) or not name:
            raise KeyError(name)
        value = self.lookup(self.user_id, name)
        if value is None:
            raise KeyError(name)
        if not isinstance(value, str):
            raise TypeError("user secret lookup must return a string or None")
        return value

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0
