from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from capability.contracts import SkillExecutor
from capability.skill_executors import create_builtin_skill_executors, load_skill_for_model_context
from skill.disclosure import (
    ProgressiveDisclosureCore,
    SkillIndex,
    SkillIndexEntry,
    skill_index_to_dict,
)


BENCHMARK_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    prompt: str
    enabled_skills: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BenchmarkCaseResult:
    name: str
    selected_skills: list[str]
    eager_context_tokens: int
    progressive_context_tokens: int
    saved_context_tokens: int
    context_savings_ratio: float


@dataclass(frozen=True)
class BenchmarkReport:
    schema_version: int
    case_results: list[BenchmarkCaseResult]
    total_eager_context_tokens: int
    total_progressive_context_tokens: int
    total_saved_context_tokens: int
    context_savings_ratio: float

class SkillBenchmark:
    def __init__(
        self,
        skill_disclosure: ProgressiveDisclosureCore,
        state_root: Path | None = None,
        skill_executors: dict[str, SkillExecutor] | None = None,
    ) -> None:
        self.skill_disclosure = skill_disclosure
        self.state_root = state_root or skill_disclosure.cache_root.parent
        self.skill_executors = skill_executors or create_builtin_skill_executors()

    def run_cases(self, cases: list[BenchmarkCase]) -> BenchmarkReport:
        if not cases:
            raise ValueError("benchmark requires at least one case")
        _reject_duplicate_case_names(cases)
        skill_index = self.skill_disclosure.prepare_skill_index()
        context_entries = [
            entry
            for entry in skill_index.entries
            if (
                entry.reference.kind in self.skill_executors
                and self.skill_executors[entry.reference.kind].adds_model_context
            )
        ]
        disclosure_index = _build_disclosure_index(skill_index, self.skill_disclosure.cache_root)
        eager_context = _join_context(
            [
                disclosure_index,
                _build_eager_context(
                    self.skill_disclosure,
                    context_entries,
                    self.skill_executors,
                    self.state_root,
                ),
            ]
        )
        results = [
            self._run_case(case, eager_context, disclosure_index)
            for case in cases
        ]
        eager_total = sum(item.eager_context_tokens for item in results)
        progressive_total = sum(item.progressive_context_tokens for item in results)
        saved_total = eager_total - progressive_total
        return BenchmarkReport(
            schema_version=BENCHMARK_SCHEMA_VERSION,
            case_results=results,
            total_eager_context_tokens=eager_total,
            total_progressive_context_tokens=progressive_total,
            total_saved_context_tokens=saved_total,
            context_savings_ratio=_savings_ratio(saved_total, eager_total),
        )

    def _run_case(
        self,
        case: BenchmarkCase,
        eager_context: str,
        disclosure_index: str,
    ) -> BenchmarkCaseResult:
        name = case.name.strip()
        prompt = case.prompt.strip()
        if not name or not prompt:
            raise ValueError("benchmark case name and prompt cannot be empty")
        selected_references = self.skill_disclosure.select_skill_references_for_prompt(
            prompt,
            case.enabled_skills,
            allowed_kinds={"prompt", "mcp"},
        )
        selected = [
            load_skill_for_model_context(
                self.skill_disclosure,
                reference,
                self.skill_executors,
                self.state_root,
            )
            for reference in selected_references
        ]
        progressive_context = _join_context(
            [disclosure_index, *[skill.instructions for skill in selected]]
        )
        eager_tokens = _estimate_tokens(eager_context)
        progressive_tokens = _estimate_tokens(progressive_context)
        saved_tokens = eager_tokens - progressive_tokens
        return BenchmarkCaseResult(
            name=name,
            selected_skills=[skill.manifest.name for skill in selected],
            eager_context_tokens=eager_tokens,
            progressive_context_tokens=progressive_tokens,
            saved_context_tokens=saved_tokens,
            context_savings_ratio=_savings_ratio(saved_tokens, eager_tokens),
        )


def benchmark_report_to_dict(report: BenchmarkReport) -> dict[str, object]:
    if report.schema_version != BENCHMARK_SCHEMA_VERSION:
        raise ValueError(
            f"benchmark schema_version {report.schema_version} requires migration to "
            f"benchmark schema_version {BENCHMARK_SCHEMA_VERSION}"
        )
    return {
        "schema_version": report.schema_version,
        "cases": [
            {
                "name": item.name,
                "selected_skills": list(item.selected_skills),
                "eager_context_tokens": item.eager_context_tokens,
                "progressive_context_tokens": item.progressive_context_tokens,
                "saved_context_tokens": item.saved_context_tokens,
                "context_savings_ratio": item.context_savings_ratio,
            }
            for item in report.case_results
        ],
        "total_eager_context_tokens": report.total_eager_context_tokens,
        "total_progressive_context_tokens": report.total_progressive_context_tokens,
        "total_saved_context_tokens": report.total_saved_context_tokens,
        "context_savings_ratio": report.context_savings_ratio,
    }


def _build_eager_context(
    disclosure: ProgressiveDisclosureCore,
    entries: list[SkillIndexEntry],
    skill_executors: dict[str, SkillExecutor],
    state_root: Path,
) -> str:
    skills = [
        load_skill_for_model_context(disclosure, entry.reference, skill_executors, state_root)
        for entry in entries
    ]
    return _join_context([skill.instructions for skill in skills])


def _build_disclosure_index(index: SkillIndex, cache_root: Path) -> str:
    text = json.dumps(
        skill_index_to_dict(index),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return text.replace(str(cache_root), "<disclosure-cache>")


def _reject_duplicate_case_names(cases: list[BenchmarkCase]) -> None:
    names = [case.name.strip() for case in cases]
    if len(names) != len(set(names)):
        raise ValueError("benchmark case names must be unique")


def _join_context(parts: list[str]) -> str:
    return "\n\n".join(part for part in parts if part)


def _estimate_tokens(text: str) -> int:
    # A fixed character ratio avoids model tokenizers and keeps reports reproducible across machines.
    return math.ceil(len(text) / 4) if text else 0


def _savings_ratio(saved_tokens: int, eager_tokens: int) -> float:
    return round(saved_tokens / eager_tokens, 4) if eager_tokens else 0.0
