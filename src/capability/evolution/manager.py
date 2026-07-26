"""Create, evaluate, promote, and roll back executable Capabilities."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from capability.evolution.candidate import (
    CapabilityCandidate,
    CapabilityCandidateRequest,
    create_capability_candidate,
    load_capability_candidate,
    verify_capability_candidate,
)
from capability.evolution.evaluation import (
    CapabilityCandidateEvaluationRequest,
    CapabilityEvaluationCase,
    CapabilityEvaluationReport,
    CapabilityEvolutionResult,
    capability_evaluation_report_to_dict,
    create_capability_report_id,
    evaluate_capability_candidate,
    read_capability_evaluation_report,
)
from capability.package import (
    CapabilityPackageManager,
    InstalledCapability,
    read_capability_package_manifest,
)
from capability.registry import CapabilityRegistry, copy_capability_registry
from provider.chat import ChatProvider
from runtime.config import AgentConfig
from runtime.evolution import EvolutionCandidateProposal, EvolutionLifecycle, EvolutionTarget
from runtime.store import RuntimeStore


@dataclass(frozen=True)
class CapabilityEvolutionRuntimeAccess:
    config: AgentConfig
    package_manager: CapabilityPackageManager
    provider: ChatProvider
    store: RuntimeStore
    read_capability_registry: Callable[[], CapabilityRegistry]
    replace_capability_registry: Callable[[CapabilityRegistry], None]


class CapabilityEvolutionManager:
    def __init__(
        self,
        runtime_access: CapabilityEvolutionRuntimeAccess,
        *,
        minimum_score: float = 0.8,
        timeout_seconds: float = 5.0,
    ) -> None:
        if minimum_score < 0 or minimum_score > 1:
            raise ValueError("minimum Capability evaluation score must be between 0 and 1")
        if timeout_seconds <= 0:
            raise ValueError("Capability evaluation timeout_seconds must be greater than zero")
        self.runtime_access = runtime_access
        self.evolution_root = runtime_access.store.private_root / "evolution"
        self.lifecycle = EvolutionLifecycle(runtime_access.store)
        self.minimum_score = minimum_score
        self.timeout_seconds = timeout_seconds

    def create_capability_candidate(
        self,
        slot: str,
        name: str,
        goal: str,
    ) -> CapabilityCandidate:
        candidate = create_capability_candidate(
            CapabilityCandidateRequest(
                package_manager=self.runtime_access.package_manager,
                registry=self.runtime_access.read_capability_registry(),
                candidate_root=self.evolution_root / "capability-candidates",
                provider=self.runtime_access.provider,
                model=self.runtime_access.config.model.model,
                slot=slot,
                name=name,
                goal=goal,
            )
        )
        try:
            manifest = read_capability_package_manifest(candidate.package_path)
            parent = self._read_candidate_parent_target(candidate)
            self.lifecycle.record_candidate_created(
                EvolutionCandidateProposal(
                    candidate_id=candidate.candidate_id,
                    target=_candidate_evolution_target(
                        candidate,
                        manifest.agent_created,
                        manifest.agent_can_update,
                    ),
                    parent=parent,
                    goal=candidate.goal,
                )
            )
        except Exception:
            if candidate.metadata_path.parent.exists():
                shutil.rmtree(candidate.metadata_path.parent)
            raise
        return candidate

    def evaluate_capability_candidate(
        self,
        candidate_id: str,
        cases: list[CapabilityEvaluationCase],
    ) -> CapabilityEvaluationReport:
        candidate = self._read_candidate(candidate_id)
        report_id = create_capability_report_id()
        report_path = (
            self.evolution_root
            / "evaluations"
            / candidate.candidate_id
            / f"{report_id}.json"
        )
        report = evaluate_capability_candidate(
            CapabilityCandidateEvaluationRequest(
                candidate=candidate,
                cases=cases,
                minimum_score=self.minimum_score,
                timeout_seconds=self.timeout_seconds,
                report_path=report_path,
                store=self.runtime_access.store,
            )
        )
        _write_json_exclusive(report.path, capability_evaluation_report_to_dict(report))
        self.lifecycle.record_candidate_evaluated(
            candidate.candidate_id,
            report.score,
            report.passed,
            report.report_id,
        )
        return report

    def promote_capability_candidate(self, candidate_id: str) -> InstalledCapability:
        candidate = self._read_candidate(candidate_id)
        report = self._read_latest_report(candidate)
        candidate_target = self._read_candidate_target(candidate)
        state = self.lifecycle.read_candidate(candidate.candidate_id)
        if state.target != candidate_target:
            raise ValueError(f"Capability candidate metadata changed: {candidate.candidate_id}")
        current_target = self._read_active_target(candidate.slot, candidate.name)
        self.lifecycle.require_candidate_can_promote(candidate.candidate_id, current_target)
        registry_before = copy_capability_registry(
            self.runtime_access.read_capability_registry()
        )
        promotion_path = self.evolution_root / "promotions" / f"{candidate.candidate_id}.json"
        if promotion_path.exists():
            raise ValueError(f"Capability candidate was already promoted: {candidate.candidate_id}")
        package_changed = False
        try:
            installed = self._install_candidate(candidate, current_target)
            package_changed = True
            self._activate_installed_capability(installed)
            _write_json_exclusive(
                promotion_path,
                {
                    "schema_version": 1,
                    "candidate_id": candidate.candidate_id,
                    "capability_key": candidate.key,
                    "version": installed.descriptor.version,
                    "report_id": report.report_id,
                    "promoted_at": _utc_now_text(),
                },
            )
            self.lifecycle.record_candidate_promoted(
                candidate.candidate_id,
                _installed_evolution_target(installed),
                current_target,
            )
            return installed
        except Exception as error:
            if package_changed:
                restoration_error = self._restore_failed_promotion(
                    candidate,
                    current_target,
                    registry_before,
                    promotion_path,
                )
                if restoration_error is not None:
                    raise RuntimeError(
                        "Capability promotion failed and automatic restoration also failed: "
                        f"{restoration_error}"
                    ) from error
            raise

    def rollback_capability(self, slot: str, name: str) -> InstalledCapability:
        package_manager = self.runtime_access.package_manager
        previous = package_manager.load_capability(slot, name)
        previous_registry = copy_capability_registry(
            self.runtime_access.read_capability_registry()
        )
        restored = package_manager.rollback_capability(slot, name)
        try:
            self._activate_installed_capability(restored)
            self.lifecycle.record_target_rolled_back(
                _installed_evolution_target(previous),
                _installed_evolution_target(restored),
            )
            return restored
        except Exception as error:
            restoration_error = self._restore_failed_rollback(previous, previous_registry)
            if restoration_error is not None:
                raise RuntimeError(
                    "Capability rollback failed and automatic restoration also failed: "
                    f"{restoration_error}"
                ) from error
            raise

    def evolve_capability(
        self,
        slot: str,
        name: str,
        goal: str,
        cases: list[CapabilityEvaluationCase],
    ) -> CapabilityEvolutionResult:
        candidate = self.create_capability_candidate(slot, name, goal)
        report = self.evaluate_capability_candidate(candidate.candidate_id, cases)
        if not report.passed:
            return CapabilityEvolutionResult(candidate, report, "rejected")
        installed = self.promote_capability_candidate(candidate.candidate_id)
        return CapabilityEvolutionResult(candidate, report, "promoted", installed)

    def _read_candidate(self, candidate_id: str) -> CapabilityCandidate:
        candidate = load_capability_candidate(
            self.evolution_root / "capability-candidates",
            candidate_id,
        )
        verify_capability_candidate(candidate)
        return candidate

    def _read_candidate_parent_target(
        self,
        candidate: CapabilityCandidate,
    ) -> EvolutionTarget | None:
        current = self._read_active_target(candidate.slot, candidate.name)
        if not candidate.parent_sha256:
            if current is not None:
                raise ValueError(f"Capability was created after proposal: {candidate.key}")
            return None
        if current is None:
            raise ValueError(f"Capability candidate parent no longer exists: {candidate.key}")
        if (
            current.version != candidate.parent_version
            or current.content_sha256 != candidate.parent_sha256
        ):
            raise ValueError(f"active Capability changed during proposal: {candidate.key}")
        return current

    def _read_candidate_target(self, candidate: CapabilityCandidate) -> EvolutionTarget:
        manifest = read_capability_package_manifest(candidate.package_path)
        return _candidate_evolution_target(
            candidate,
            manifest.agent_created,
            manifest.agent_can_update,
        )

    def _read_active_target(self, slot: str, name: str) -> EvolutionTarget | None:
        registration = self.runtime_access.read_capability_registry().find_capability(slot)
        if registration is None or registration.descriptor.source != "local":
            return None
        if registration.descriptor.name != name:
            raise ValueError(
                f"Capability slot {slot} is active as {registration.descriptor.name}, not {name}"
            )
        installed = self.runtime_access.package_manager.load_capability(slot, name)
        if installed.descriptor != registration.descriptor:
            raise ValueError(f"installed Capability does not match Agent registry: {slot}:{name}")
        return _installed_evolution_target(installed)

    def _read_latest_report(
        self,
        candidate: CapabilityCandidate,
    ) -> CapabilityEvaluationReport:
        root = self.evolution_root / "evaluations" / candidate.candidate_id
        reports = (
            [read_capability_evaluation_report(path) for path in root.glob("report-*.json")]
            if root.is_dir()
            else []
        )
        matching = [
            report
            for report in reports
            if report.candidate_sha256 == candidate.candidate_sha256
        ]
        if not matching:
            raise ValueError(
                f"Capability candidate has not been evaluated: {candidate.candidate_id}"
            )
        return max(matching, key=lambda item: (item.created_at, item.report_id))

    def _install_candidate(
        self,
        candidate: CapabilityCandidate,
        current_target: EvolutionTarget | None,
    ) -> InstalledCapability:
        source = str(candidate.package_path)
        if current_target is None:
            return self.runtime_access.package_manager.install_capability(source)
        return self.runtime_access.package_manager.update_capability(
            candidate.slot,
            candidate.name,
            source,
        )

    def _activate_installed_capability(self, installed: InstalledCapability) -> None:
        registry = copy_capability_registry(self.runtime_access.read_capability_registry())
        registry.register_capability(
            installed.manifest.slot,
            installed.implementation,
            installed.descriptor,
            replace=True,
        )
        registry.validate_dependencies()
        self.runtime_access.replace_capability_registry(registry)

    def _restore_failed_promotion(
        self,
        candidate: CapabilityCandidate,
        current_target: EvolutionTarget | None,
        registry: CapabilityRegistry,
        promotion_path: Path,
    ) -> Exception | None:
        errors: list[Exception] = []
        try:
            if current_target is None:
                self.runtime_access.package_manager.remove_capability(
                    candidate.slot,
                    candidate.name,
                )
            else:
                self.runtime_access.package_manager.rollback_capability(
                    candidate.slot,
                    candidate.name,
                )
        except Exception as error:
            errors.append(error)
        try:
            self.runtime_access.replace_capability_registry(registry)
        except Exception as error:
            errors.append(error)
        if promotion_path.exists():
            promotion_path.unlink()
        return errors[0] if errors else None

    def _restore_failed_rollback(
        self,
        previous: InstalledCapability,
        registry: CapabilityRegistry,
    ) -> Exception | None:
        errors: list[Exception] = []
        try:
            self.runtime_access.package_manager.update_capability(
                previous.manifest.slot,
                previous.manifest.name,
                str(previous.manifest.path),
            )
        except Exception as error:
            errors.append(error)
        try:
            self.runtime_access.replace_capability_registry(registry)
        except Exception as error:
            errors.append(error)
        return errors[0] if errors else None


def _candidate_evolution_target(
    candidate: CapabilityCandidate,
    agent_created: bool,
    agent_can_update: bool,
) -> EvolutionTarget:
    return EvolutionTarget(
        target_type="capability",
        key=candidate.key,
        name=candidate.name,
        version=candidate.proposed_version,
        content_sha256=candidate.candidate_sha256,
        agent_created=agent_created,
        agent_can_update=agent_can_update,
    )


def _installed_evolution_target(installed: InstalledCapability) -> EvolutionTarget:
    descriptor = installed.descriptor
    return EvolutionTarget(
        target_type="capability",
        key=descriptor.key,
        name=descriptor.name,
        version=descriptor.version,
        content_sha256=descriptor.content_sha256,
        agent_created=descriptor.agent_created,
        agent_can_update=descriptor.agent_can_update,
    )


def _write_json_exclusive(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
