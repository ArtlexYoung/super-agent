"""Agent Runtime 的最小实现。"""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass

__version__ = "0.2.45"


def require_text(value: object, name: str) -> str:
    """读取去除首尾空白的非空文本。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value.strip()


def require_boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def require_integer(value: object, name: str, minimum: int = 0, maximum: int | None = None) -> int:
    invalid = isinstance(value, bool) or not isinstance(value, int) or value < minimum
    if invalid or maximum is not None and value > maximum:
        limit = f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
        raise ValueError(f"{name} must be an integer {limit}")
    return value


def require_number(value: object, name: str, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    selected = float(value)
    if minimum is not None and selected < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and selected > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return selected


def require_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def reject_unknown_fields(value: Mapping[str, object], allowed: set[str], name: str) -> None:
    if unknown := sorted(set(value) - allowed):
        raise ValueError(f"unknown {name} fields: {', '.join(unknown)}")


def dataclass_data(value: object) -> dict[str, object]:
    """将数据类转换为不共享可变容器的普通 JSON 结构。"""
    if isinstance(value, type) or not is_dataclass(value):
        raise TypeError("value must be a dataclass instance")
    return {item.name: _plain_data(getattr(value, item.name)) for item in fields(value)}


def _plain_data(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return dataclass_data(value)
    if isinstance(value, Mapping):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_data(item) for item in value]
    return value
