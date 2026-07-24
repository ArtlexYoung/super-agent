"""Deterministic Skill freshness derived from runtime evaluation records."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from runtime.evaluation import EvaluationRecord, EvaluationRecordStore
from skill.manifest import DEFAULT_SKILL_FRESHNESS


STATS_FILE = "skill_stats.json"
FOLLOWUP_WINDOW_MINUTES = 10


class SkillFreshnessStore:
    def __init__(self, evaluation_root: Path, cache_root: Path) -> None:
        self.evaluation_root = evaluation_root
        self.cache_root = cache_root
        self.stats_path = cache_root / STATS_FILE

    def read_skill_stats(self) -> dict[str, dict[str, Any]]:
        records = EvaluationRecordStore(self.evaluation_root).read_evaluation_records(
            target_type="skill",
            source_type="agent_run",
        )
        data = _build_store_data(records, datetime.now(UTC))
        self._write_store_data(data)
        return data["skills"]

    def _write_store_data(self, data: dict[str, Any]) -> None:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
        self.stats_path.write_text(text, encoding="utf-8")


def _build_store_data(
    records: list[EvaluationRecord],
    current_time: datetime,
) -> dict[str, Any]:
    data = _default_store_data()
    ordered = sorted(
        records,
        key=lambda record: (_parse_datetime(record.created_at), record.record_id),
    )
    for record in ordered:
        event = _event_from_evaluation_record(record)
        _record_same_function_followup(data, event)
        skill_stats = _get_or_create_skill_stats(data, event)
        _apply_skill_run(skill_stats, event)
        data["last_skill_by_function_group"][event["function_group"]] = {
            "skill": event["skill"],
            "called_at": event["called_at"],
            "run_id": event["run_id"],
        }
    for stats in data["skills"].values():
        _update_freshness(stats, current_time)
    return data


def _event_from_evaluation_record(record: EvaluationRecord) -> dict[str, Any]:
    result = record.result
    return {
        "skill": record.target.key,
        "function_group": record.target.function_group,
        "called_at": record.created_at,
        "run_id": record.source.run_id,
        "input_tokens": result.token_usage.input_tokens,
        "output_tokens": result.token_usage.output_tokens,
        "latency_ms": result.latency_ms,
        "success": result.success,
        "score": result.score,
        "error_type": result.error_type,
        "empty_output": result.token_usage.output_tokens == 0,
    }


def _record_same_function_followup(data: dict[str, Any], event: dict[str, Any]) -> None:
    previous = data["last_skill_by_function_group"].get(event["function_group"])
    if not _is_valid_followup(previous, event):
        return
    previous_stats = data["skills"].get(previous["skill"])
    if not isinstance(previous_stats, dict):
        return
    previous_stats["same_function_followups"] = int(previous_stats["same_function_followups"]) + 1
    if bool(event["success"]):
        previous_stats["same_function_successful_followups"] = (
            int(previous_stats["same_function_successful_followups"]) + 1
        )


def _apply_skill_run(stats: dict[str, Any], event: dict[str, Any]) -> None:
    if int(stats["call_count"]) == 0:
        stats["first_used_at"] = event["called_at"]
    stats["call_count"] = int(stats["call_count"]) + 1
    stats["success_count"] = int(stats["success_count"]) + int(bool(event["success"]))
    stats["error_count"] = int(stats["error_count"]) + int(bool(event["error_type"]))
    stats["empty_output_count"] = int(stats["empty_output_count"]) + int(
        bool(event["empty_output"])
    )
    stats["total_input_tokens"] = int(stats["total_input_tokens"]) + int(
        event["input_tokens"]
    )
    stats["total_output_tokens"] = int(stats["total_output_tokens"]) + int(
        event["output_tokens"]
    )
    if event["latency_ms"] is not None:
        stats["total_latency_ms"] = int(stats["total_latency_ms"]) + int(
            event["latency_ms"]
        )
        stats["latency_sample_count"] = int(stats["latency_sample_count"]) + 1
    stats["last_used_at"] = event["called_at"]
    stats["success_ewma"] = _update_ewma(
        float(stats["success_ewma"]),
        _event_reward(event),
        int(stats["call_count"]),
    )


def _update_freshness(stats: dict[str, Any], now: datetime) -> None:
    scores = _score_components(stats, now)
    base = (
        0.30 * scores["quality"]
        + 0.20 * scores["recency"]
        + 0.15 * scores["frequency"]
        + 0.15 * scores["efficiency"]
        + 0.10 * scores["reliability"]
        + 0.10 * scores["replacement"]
    )
    confidence = scores["confidence"] / 100
    freshness = confidence * base + (1 - confidence) * DEFAULT_SKILL_FRESHNESS
    stats["freshness"] = round(_clamp(freshness, 0, 100), 2)
    stats["freshness_updated_at"] = _format_datetime(now)


def _score_components(stats: dict[str, Any], now: datetime) -> dict[str, float]:
    call_count = int(stats["call_count"])
    first_used_at = _parse_datetime(str(stats["first_used_at"] or stats["last_used_at"]))
    last_used_at = _parse_datetime(str(stats["last_used_at"]))
    total_tokens = int(stats["total_input_tokens"]) + int(stats["total_output_tokens"])
    average_tokens = total_tokens / max(call_count, 1)
    followups = int(stats["same_function_followups"])
    successful_followups = int(stats["same_function_successful_followups"])
    days_since_last_used = max(0.0, (now - last_used_at).total_seconds() / 86400)
    days_active = max(1.0, (now - first_used_at).total_seconds() / 86400)
    calls_per_week = call_count / days_active * 7
    return {
        "quality": float(stats["success_ewma"]) * 100,
        "recency": math.exp(-days_since_last_used / 7) * 100,
        "frequency": _clamp(calls_per_week * 20, 0, 100),
        "efficiency": _efficiency_score(stats, average_tokens),
        "reliability": _reliability_score(stats),
        "replacement": 100 if followups == 0 else 100 * (1 - successful_followups / followups),
        "confidence": 100 * call_count / (call_count + 8),
    }


def _efficiency_score(stats: dict[str, Any], average_tokens: float) -> float:
    token_score = _clamp(100 - max(0, average_tokens - 1500) / 85, 0, 100)
    latency_samples = int(stats["latency_sample_count"])
    if latency_samples == 0:
        return token_score
    average_latency = int(stats["total_latency_ms"]) / latency_samples
    latency_score = _clamp(100 - max(0, average_latency - 1000) / 90, 0, 100)
    return 0.7 * token_score + 0.3 * latency_score


def _reliability_score(stats: dict[str, Any]) -> float:
    call_count = max(int(stats["call_count"]), 1)
    success_rate = int(stats["success_count"]) / call_count
    empty_rate = int(stats["empty_output_count"]) / call_count
    error_rate = int(stats["error_count"]) / call_count
    return _clamp((success_rate - 0.3 * empty_rate - 0.5 * error_rate) * 100, 0, 100)


def _event_reward(event: dict[str, Any]) -> float:
    if not bool(event["success"]):
        return 0.0
    reward = float(event["score"])
    if bool(event["empty_output"]):
        reward = min(reward, 0.2)
    total_tokens = int(event["input_tokens"]) + int(event["output_tokens"])
    if total_tokens > 12000:
        reward *= 0.7
    return _clamp(reward, 0, 1)


def _update_ewma(previous: float, value: float, call_count: int) -> float:
    if call_count <= 1:
        return value
    return 0.75 * previous + 0.25 * value


def _is_valid_followup(previous: object, event: dict[str, Any]) -> bool:
    if not isinstance(previous, dict):
        return False
    if previous.get("skill") == event["skill"] or previous.get("run_id") == event["run_id"]:
        return False
    previous_time = _parse_datetime(str(previous.get("called_at", "")))
    event_time = _parse_datetime(event["called_at"])
    elapsed = event_time - previous_time
    return timedelta(0) <= elapsed <= timedelta(minutes=FOLLOWUP_WINDOW_MINUTES)


def _get_or_create_skill_stats(data: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    skills = data["skills"]
    if event["skill"] not in skills:
        skills[event["skill"]] = _new_skill_stats(event["skill"], event["function_group"])
    return skills[event["skill"]]


def _new_skill_stats(skill_name: str, function_group: str) -> dict[str, Any]:
    return {
        "skill": skill_name,
        "function_group": function_group,
        "freshness": DEFAULT_SKILL_FRESHNESS,
        "freshness_updated_at": "",
        "call_count": 0,
        "success_count": 0,
        "error_count": 0,
        "empty_output_count": 0,
        "success_ewma": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_latency_ms": 0,
        "latency_sample_count": 0,
        "same_function_followups": 0,
        "same_function_successful_followups": 0,
        "first_used_at": "",
        "last_used_at": "",
    }


def _default_store_data() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "skills": {},
        "last_skill_by_function_group": {},
    }


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)
