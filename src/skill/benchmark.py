from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from typing import Any
from capability.skill_executors import create_builtin_skill_executors, load_skill_for_model_context
from skill.disclosure import (
    ProgressiveDisclosureCore,
    SkillIndex,
    SkillIndexEntry,
)


BENCHMARK_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    prompt: str
    enabled_skills: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BenchmarkCaseResult:
    name: str
    selected_skills: list[str]
    no_skill_context_tokens: int
    eager_context_tokens: int
    progressive_context_tokens: int
    eager_skill_overhead_tokens: int
    progressive_skill_overhead_tokens: int
    saved_context_tokens: int
    context_savings_ratio: float


@dataclass(frozen=True)
class BenchmarkReport:
    schema_version: int
    input_sha256: str
    skill_count: int
    case_results: list[BenchmarkCaseResult]
    total_no_skill_context_tokens: int
    total_eager_context_tokens: int
    total_progressive_context_tokens: int
    total_eager_skill_overhead_tokens: int
    total_progressive_skill_overhead_tokens: int
    total_saved_context_tokens: int
    context_savings_ratio: float


class SkillBenchmark:
    def __init__(
        self,
        skill_disclosure: ProgressiveDisclosureCore,
        skill_executors: dict[str, Any] | None = None,
        *,
        base_system_prompt: str = "",
    ) -> None:
        self.skill_disclosure = skill_disclosure
        self.skill_executors = skill_executors or create_builtin_skill_executors()
        self.base_system_prompt = base_system_prompt.strip()

    def run_cases(self, cases: list[BenchmarkCase]) -> BenchmarkReport:
        if not cases:
            raise ValueError("benchmark requires at least one case")
        _reject_duplicate_case_names(cases)
        skill_index = self.skill_disclosure.prepare_skill_index()
        context_entries = [
            entry
            for entry in skill_index.entries
            if (
                entry.reference.capability in self.skill_executors
                and self.skill_executors[entry.reference.capability].adds_model_context
            )
        ]
        disclosure_index = _build_disclosure_index(skill_index, self.skill_disclosure.cache_root)
        eager_skill_context = _join_context(
            [
                disclosure_index,
                _build_eager_context(
                    self.skill_disclosure,
                    context_entries,
                    self.skill_executors,
                ),
            ]
        )
        results = [
            self._run_case(case, eager_skill_context, disclosure_index)
            for case in cases
        ]
        no_skill_total = sum(item.no_skill_context_tokens for item in results)
        eager_total = sum(item.eager_context_tokens for item in results)
        progressive_total = sum(item.progressive_context_tokens for item in results)
        eager_overhead_total = eager_total - no_skill_total
        progressive_overhead_total = progressive_total - no_skill_total
        saved_total = eager_total - progressive_total
        return BenchmarkReport(
            schema_version=BENCHMARK_SCHEMA_VERSION,
            input_sha256=_create_benchmark_input_sha256(
                cases,
                skill_index,
                self.base_system_prompt,
            ),
            skill_count=len(context_entries),
            case_results=results,
            total_no_skill_context_tokens=no_skill_total,
            total_eager_context_tokens=eager_total,
            total_progressive_context_tokens=progressive_total,
            total_eager_skill_overhead_tokens=eager_overhead_total,
            total_progressive_skill_overhead_tokens=progressive_overhead_total,
            total_saved_context_tokens=saved_total,
            context_savings_ratio=_savings_ratio(saved_total, eager_total),
        )

    def _run_case(
        self,
        case: BenchmarkCase,
        eager_skill_context: str,
        disclosure_index: str,
    ) -> BenchmarkCaseResult:
        name = case.name.strip()
        prompt = case.prompt.strip()
        if not name or not prompt:
            raise ValueError("benchmark case name and prompt cannot be empty")
        selected_references = self.skill_disclosure.select_skill_references_for_prompt(
            prompt,
            case.enabled_skills,
            allowed_capabilities={
                name
                for name, executor in self.skill_executors.items()
                if executor.adds_model_context
            },
        )
        selected = [
            load_skill_for_model_context(
                self.skill_disclosure,
                reference,
                self.skill_executors,
                store=self.skill_disclosure.store,
            )
            for reference in selected_references
        ]
        progressive_context = _join_context(
            [
                self.base_system_prompt,
                prompt,
                disclosure_index,
                *[skill.instructions for skill in selected],
            ]
        )
        no_skill_context = _join_context([self.base_system_prompt, prompt])
        eager_context = _join_context(
            [self.base_system_prompt, prompt, eager_skill_context]
        )
        no_skill_tokens = _estimate_tokens(no_skill_context)
        eager_tokens = _estimate_tokens(eager_context)
        progressive_tokens = _estimate_tokens(progressive_context)
        saved_tokens = eager_tokens - progressive_tokens
        return BenchmarkCaseResult(
            name=name,
            selected_skills=[skill.manifest.name for skill in selected],
            no_skill_context_tokens=no_skill_tokens,
            eager_context_tokens=eager_tokens,
            progressive_context_tokens=progressive_tokens,
            eager_skill_overhead_tokens=eager_tokens - no_skill_tokens,
            progressive_skill_overhead_tokens=progressive_tokens - no_skill_tokens,
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
        "input_sha256": report.input_sha256,
        "skill_count": report.skill_count,
        "cases": [
            {
                "name": item.name,
                "selected_skills": list(item.selected_skills),
                "context_tokens": {
                    "no_skill": item.no_skill_context_tokens,
                    "eager_skill": item.eager_context_tokens,
                    "progressive_skill": item.progressive_context_tokens,
                },
                "skill_overhead_tokens": {
                    "eager_skill": item.eager_skill_overhead_tokens,
                    "progressive_skill": item.progressive_skill_overhead_tokens,
                },
                "progressive_vs_eager": {
                    "saved_context_tokens": item.saved_context_tokens,
                    "context_savings_ratio": item.context_savings_ratio,
                },
            }
            for item in report.case_results
        ],
        "totals": {
            "context_tokens": {
                "no_skill": report.total_no_skill_context_tokens,
                "eager_skill": report.total_eager_context_tokens,
                "progressive_skill": report.total_progressive_context_tokens,
            },
            "skill_overhead_tokens": {
                "eager_skill": report.total_eager_skill_overhead_tokens,
                "progressive_skill": report.total_progressive_skill_overhead_tokens,
            },
            "progressive_vs_eager": {
                "saved_context_tokens": report.total_saved_context_tokens,
                "context_savings_ratio": report.context_savings_ratio,
            },
        },
    }


def _build_eager_context(
    disclosure: ProgressiveDisclosureCore,
    entries: list[SkillIndexEntry],
    skill_executors: dict[str, Any],
) -> str:
    skills = [
        load_skill_for_model_context(
            disclosure,
            entry.reference,
            skill_executors,
            store=disclosure.store,
        )
        for entry in entries
    ]
    return _join_context([skill.instructions for skill in skills])


def _build_disclosure_index(index: SkillIndex, cache_root: Path) -> str:
    text = index.build_prompt_with_cache_paths()
    return text.replace(str(cache_root), "<disclosure-cache>")


def _create_benchmark_input_sha256(
    cases: list[BenchmarkCase],
    index: SkillIndex,
    base_system_prompt: str,
) -> str:
    value = {
        "cases": [asdict(case) for case in cases],
        "skill_content": [
            {
                "key": entry.reference.key,
                "content_sha256": entry.content_sha256,
            }
            for entry in index.entries
        ],
        "system_prompt": base_system_prompt,
    }
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _reject_duplicate_case_names(cases: list[BenchmarkCase]) -> None:
    names = [case.name.strip() for case in cases]
    if len(names) != len(set(names)):
        raise ValueError("benchmark case names must be unique")


def _join_context(parts: list[str]) -> str:
    return "\n\n".join(part for part in parts if part)


def _estimate_tokens(text: str) -> int:
    # A fixed character ratio avoids model tokenizers and stays reproducible.
    return math.ceil(len(text) / 4) if text else 0


def _savings_ratio(saved_tokens: int, eager_tokens: int) -> float:
    return round(saved_tokens / eager_tokens, 4) if eager_tokens else 0.0
