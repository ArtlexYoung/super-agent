"""CLI-only configuration and terminal confirmation behavior."""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from core.checks import (
    ActionDecision,
    ActionDecisionType,
    ActionRequest,
    ActionRules,
)
from core.config import (
    CommonConfig,
    find_optional_config_file,
    reject_unknown_settings,
    require_config_header,
)
from core.models import LOCAL_USER_ID
from core.state.store import EventStore
from super_agent import Agent


CommonConfigSource = CommonConfig | str | Path | None


@dataclass(frozen=True)
class CliConfig:
    """Terminal behavior independent from Agent and task configuration."""

    user_id: str
    output: str
    save: bool
    show_summary: bool
    source: Path

    @classmethod
    def load_from_file(cls, path: str | Path) -> "CliConfig":
        source = Path(path).expanduser().absolute()
        data = tomllib.loads(source.read_text(encoding="utf-8"))
        require_config_header(data, "cli")
        reject_unknown_settings(
            data,
            {"schema_version", "kind", "run"},
            "CLI configuration tables",
        )
        run = data.get("run", {})
        if not isinstance(run, dict):
            raise ValueError("CLI run settings must be a table")
        reject_unknown_settings(
            run,
            {"user_id", "output", "save", "show_summary"},
            "CLI run settings",
        )
        user_id = str(run.get("user_id", LOCAL_USER_ID)).strip()
        if not user_id:
            raise ValueError("CLI run user_id cannot be empty")
        output = str(run.get("output", "text")).strip().lower()
        if output not in {"text", "json"}:
            raise ValueError("CLI run output must be text or json")
        return cls(
            user_id,
            output,
            _read_cli_boolean(run.get("save", False), "save"),
            _read_cli_boolean(run.get("show_summary", True), "show_summary"),
            source,
        )

    @classmethod
    def load_automatically(
        cls,
        base_directory: str | Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> "CliConfig":
        base, source = find_optional_config_file(
            "cli.toml",
            "SUPER_AGENT_CLI_CONFIG",
            base_directory=base_directory,
            environment=environment,
        )
        return cls.load_from_file(source) if source else cls.create_default(base)

    @classmethod
    def create_default(cls, base_directory: str | Path | None = None) -> "CliConfig":
        base = (
            Path.cwd()
            if base_directory is None
            else Path(base_directory).expanduser().absolute()
        )
        return cls(LOCAL_USER_ID, "text", False, True, base / "cli.toml")


class TerminalActionRules(ActionRules):
    """Ask before a CLI action leaves read-only state."""

    def check_action(self, request: ActionRequest) -> ActionDecision:
        decision = super().check_action(request)
        if decision.decision != ActionDecisionType.REQUIRE_CONFIRMATION:
            return decision
        effects = ", ".join(effect.value for effect in request.effects)
        print(
            f"Allow {effects} on {request.resource}? [y/N]",
            file=sys.stderr,
            end=" ",
            flush=True,
        )
        try:
            answer = sys.stdin.readline().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer in {"y", "yes"}:
            return ActionDecision(
                ActionDecisionType.ALLOW,
                "terminal user confirmed the action",
                True,
            )
        return decision


def load_cli_config(source: str | Path | None = None) -> CliConfig:
    return CliConfig.load_automatically() if source is None else CliConfig.load_from_file(source)


def load_common_config(source: CommonConfigSource = None) -> CommonConfig:
    if source is None:
        return CommonConfig.load_automatically()
    if isinstance(source, CommonConfig):
        return source
    return CommonConfig.load_from_file(source)


def load_agent(
    source: CommonConfigSource = None,
    *,
    use_storage: bool = True,
) -> Agent:
    return Agent(
        load_common_config(source),
        use_storage=use_storage,
        action_rules=TerminalActionRules(),
    )


def load_event_store(
    source: CommonConfigSource = None,
    user_id: str = LOCAL_USER_ID,
) -> EventStore:
    from adapter.storage import DisclosureStorage, create_storage_backend

    config = load_common_config(source)
    backend = create_storage_backend(
        config.storage.backend,
        str(config.storage.path),
        config.storage.url_env,
    )
    return EventStore(
        backend,
        config.storage.path,
        user_id,
        config.agent.name,
        disclosure_factory=DisclosureStorage,
    )


def configure_config_parser(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(cli_config=None)
    subparsers = parser.add_subparsers(dest="config_command")
    for name, help_text in (
        ("show", "show active CLI settings"),
        ("validate", "validate CLI settings"),
    ):
        subparsers.add_parser(name, help=help_text).add_argument("--cli-config")


def run_config_command(args: argparse.Namespace) -> int:
    config = load_cli_config(args.cli_config)
    if args.config_command == "validate":
        print(f"OK  CLI configuration: {_cli_source_label(config)}")
    else:
        _print_cli_config(config)
    return 0


def _print_cli_config(config: CliConfig) -> None:
    print(f"CLI configuration: {_cli_source_label(config)}")
    print(f"run.user_id = {config.user_id}")
    print(f"run.output = {config.output}")
    print(f"run.save = {str(config.save).lower()}")
    print(f"run.show_summary = {str(config.show_summary).lower()}")


def _cli_source_label(config: CliConfig) -> str:
    return str(config.source) if config.source.is_file() else "built-in defaults"


def _read_cli_boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"CLI run {name} must be true or false")
    return value
