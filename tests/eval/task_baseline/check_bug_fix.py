"""工作区外的行为检查；只接受本目录受控样本，不是执行沙箱。"""

import copy
import importlib.util
import sys
from pathlib import Path


def check(workspace: Path) -> None:
    spec = importlib.util.spec_from_file_location("event_window", workspace / "event_window.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    events = [{"timestamp": value, "id": index} for index, value in enumerate((3, -1, 2, 2, 0, 4))]
    original = copy.deepcopy(events)
    for values in ([], events):
        for start, end in ((0, 3), (2, 2), (4, 0), (-1, 0), (-3, 9), (2, 4)):
            expected = [item for item in values if start <= item["timestamp"] < end]
            actual = module.select_events(values, start, end)
            if actual != expected or events != original:
                raise AssertionError(f"wrong selection or input mutation: [{start}, {end})")


if __name__ == "__main__":
    check(Path(sys.argv[1]))
    print("12 behavioral cases passed")
