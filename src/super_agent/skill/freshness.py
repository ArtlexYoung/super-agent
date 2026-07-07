from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


EVENTS_FILE = "skill_events.jsonl"
STATS_FILE = "skill_stats.json"
DEFAULT_FRESHNESS = 70.0
FOLLOWUP_WINDOW_MINUTES = 10


@dataclass(frozen=True)
class SkillRunRecord:
    skill_name: str
    function_group: str
    input_text: str
    output_text: str
    success: bool
    called_at: datetime | None = None
    latency_ms: int | None = None
    user_feedback: int | None = None
    error_type: str = ""


class SkillFreshnessStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.events_path = root / EVENTS_FILE
        self.stats_path = root / STATS_FILE

    def record_skill_run(self, record: SkillRunRecord) -> None:
        called_at = _normalize_datetime(record.called_at)
        data = self._read_store_data()
        normalized = _normalize_record(record, called_at)
        self._record_same_function_followup(data, normalized)
        skill_stats = _get_or_create_skill_stats(data, normalized)
        _apply_skill_run(skill_stats, normalized)
        data["last_skill_by_function_group"][normalized["function_group"]] = {
            "skill": normalized["skill"],
            "called_at": normalized["called_at"],
        }
        self._append_event(normalized)
        self._write_store_data(data)

    def read_skill_stats(self) -> dict[str, dict[str, Any]]:
        return self._read_store_data()["skills"]

    def _record_same_function_followup(self, data: dict[str, Any], event: dict[str, Any]) -> None:
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
        _update_freshness(previous_stats, _parse_datetime(event["called_at"]))

    def _append_event(self, event: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def _read_store_data(self) -> dict[str, Any]:
        if not self.stats_path.exists():
            return _default_store_data()
        data = json.loads(self.stats_path.read_text(encoding="utf-8"))
        return _normalize_store_data(data)

    def _write_store_data(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
        self.stats_path.write_text(text, encoding="utf-8")


def _normalize_record(record: SkillRunRecord, called_at: datetime) -> dict[str, Any]:
    input_tokens = _estimate_tokens(record.input_text)
    output_tokens = _estimate_tokens(record.output_text)
    return {
        "skill": record.skill_name,
        "function_group": record.function_group or record.skill_name,
        "called_at": _format_datetime(called_at),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": record.latency_ms,
        "success": record.success,
        "error_type": record.error_type,
        "empty_output": not record.output_text.strip(),
        "user_feedback": record.user_feedback,
    }


def _apply_skill_run(stats: dict[str, Any], event: dict[str, Any]) -> None:
    now = _parse_datetime(event["called_at"])
    if int(stats["call_count"]) == 0:
        stats["first_used_at"] = event["called_at"]
    stats["call_count"] = int(stats["call_count"]) + 1
    stats["success_count"] = int(stats["success_count"]) + int(bool(event["success"]))
    stats["empty_output_count"] = int(stats["empty_output_count"]) + int(bool(event["empty_output"]))
    stats["total_input_tokens"] = int(stats["total_input_tokens"]) + int(event["input_tokens"])
    stats["total_output_tokens"] = int(stats["total_output_tokens"]) + int(event["output_tokens"])
    stats["last_used_at"] = event["called_at"]
    stats["success_ewma"] = _update_ewma(float(stats["success_ewma"]), _event_reward(event), int(stats["call_count"]))
    _update_freshness(stats, now)


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
    freshness = confidence * base + (1 - confidence) * DEFAULT_FRESHNESS
    stats["freshness"] = round(_clamp(freshness, 0, 100), 2)
    stats["freshness_updated_at"] = _format_datetime(now)


def _score_components(stats: dict[str, Any], now: datetime) -> dict[str, float]:
    call_count = int(stats["call_count"])
    first_used_at = _parse_datetime(str(stats["first_used_at"] or stats["last_used_at"]))
    last_used_at = _parse_datetime(str(stats["last_used_at"]))
    average_tokens = (int(stats["total_input_tokens"]) + int(stats["total_output_tokens"])) / max(call_count, 1)
    followups = int(stats["same_function_followups"])
    successful_followups = int(stats["same_function_successful_followups"])
    days_since_last_used = max(0.0, (now - last_used_at).total_seconds() / 86400)
    days_active = max(1.0, (now - first_used_at).total_seconds() / 86400)
    calls_per_week = call_count / days_active * 7
    return {
        "quality": float(stats["success_ewma"]) * 100,
        "recency": math.exp(-days_since_last_used / 7) * 100,
        "frequency": _clamp(calls_per_week * 20, 0, 100),
        "efficiency": _clamp(100 - max(0, average_tokens - 1500) / 85, 0, 100),
        "reliability": _reliability_score(stats),
        "replacement": 100 if followups == 0 else 100 * (1 - successful_followups / followups),
        "confidence": 100 * call_count / (call_count + 8),
    }


def _reliability_score(stats: dict[str, Any]) -> float:
    call_count = max(int(stats["call_count"]), 1)
    success_rate = int(stats["success_count"]) / call_count
    empty_rate = int(stats["empty_output_count"]) / call_count
    return _clamp((success_rate - 0.5 * empty_rate) * 100, 0, 100)


def _event_reward(event: dict[str, Any]) -> float:
    if not bool(event["success"]):
        return 0.0
    if bool(event["empty_output"]):
        return 0.2
    total_tokens = int(event["input_tokens"]) + int(event["output_tokens"])
    reward = 0.7 if total_tokens > 12000 else 1.0
    feedback = event.get("user_feedback")
    if feedback is not None:
        reward += 0.2 if int(feedback) > 0 else -0.3
    return _clamp(reward, 0, 1)


def _update_ewma(previous: float, value: float, call_count: int) -> float:
    if call_count <= 1:
        return value
    return 0.75 * previous + 0.25 * value


def _is_valid_followup(previous: object, event: dict[str, Any]) -> bool:
    if not isinstance(previous, dict) or previous.get("skill") == event["skill"]:
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
        "freshness": DEFAULT_FRESHNESS,
        "freshness_updated_at": "",
        "call_count": 0,
        "success_count": 0,
        "empty_output_count": 0,
        "success_ewma": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "same_function_followups": 0,
        "same_function_successful_followups": 0,
        "first_used_at": "",
        "last_used_at": "",
    }


def _default_store_data() -> dict[str, Any]:
    return {"version": 1, "skills": {}, "last_skill_by_function_group": {}}


def _normalize_store_data(data: dict[str, Any]) -> dict[str, Any]:
    normalized = _default_store_data()
    normalized["version"] = int(data.get("version", 1))
    normalized["skills"] = dict(data.get("skills", {}))
    normalized["last_skill_by_function_group"] = dict(data.get("last_skill_by_function_group", {}))
    return normalized


def _estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def _normalize_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _parse_datetime(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)
