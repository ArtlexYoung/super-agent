"""受控的真实 stdio 故障进程，仅由本地测试启动。"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

root = Path(sys.argv[1])


def settings():
    return json.loads((root / "settings.json").read_text())


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    with (root / "received.jsonl").open("a") as log:
        log.write(json.dumps({"pid": os.getpid(), "method": request["method"],
                              "name": request.get("params", {}).get("name", "")}) + "\n")
    config = settings()
    method = request["method"]
    if method == "notifications/initialized":
        continue
    response = {"jsonrpc": "2.0", "id": request["id"]}
    if method == "initialize":
        time.sleep(config.get("init_delay", 0))
        if config.get("reject_initialize"):
            emit({**response, "error": {"code": -32001, "message": "rejected"}})
        else:
            emit({**response, "result": {"serverInfo": {"name": "fixture"}}})
        continue
    mode = config.get("mode", "normal")
    if method == "tools/call":
        mode = request["params"]["name"]
    time.sleep(config.get("reply_delay", 0))
    if mode in {"hang", "ignore_term", "partial"}:
        if mode == "ignore_term":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if mode == "partial":
            os.write(sys.stdout.fileno(), b'{"jsonrpc":"2.0",')
        time.sleep(30)
    elif mode == "notifications":
        while True:
            emit({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
            time.sleep(0.002)
    elif mode == "exit":
        os._exit(17)
    elif mode == "mismatch":
        emit({**response, "id": 999, "result": {"tools": []}})
        continue
    elif mode == "invalid":
        emit({**response, "result": []})
        continue
    elif mode == "invalid_error":
        emit({**response, "error": {"message": "missing code"}})
        continue
    elif mode == "nonfinite":
        emit({**response, "result": {"value": float("nan")}})
        continue
    elif mode == "oversize":
        emit({**response, "result": {"content": "x" * 100_000}})
        continue
    elif mode == "remote_error":
        emit({**response, "error": {"code": -32000, "message": "operation rejected"}})
        continue
    elif mode == "stderr":
        sys.stderr.buffer.write(b"diagnostic\n" * 200_000)
        sys.stderr.flush()
    elif mode == "late":
        time.sleep(0.4)
    elif mode == "descendant":
        child_code = (
            "import signal,time,pathlib,sys; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "pathlib.Path(sys.argv[1]).write_text(str(__import__('os').getpid())); "
            "time.sleep(30)"
        )
        subprocess.Popen([sys.executable, "-I", "-B", "-c", child_code, str(root / "child.pid")])
        while not (root / "child.pid").exists():
            time.sleep(0.001)
    if method == "tools/list":
        result = {"tools": [{"name": "work", "description": "fixture", "inputSchema": {"type": "object"}}]}
        emit({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
    else:
        result = {"content": [{"type": "text", "text": "健康"}], "isError": False}
    if mode == "split_utf8":
        data = (json.dumps({**response, "result": result}, ensure_ascii=False) + "\n").encode()
        for byte in data:
            os.write(sys.stdout.fileno(), bytes([byte]))
    else:
        emit({**response, "result": result})
    if mode == "no_read_after_reply":
        time.sleep(30)
