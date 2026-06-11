#!/usr/bin/env python3
"""
MCP Indicator Reader — connects to the TradingView MCP server via stdio,
launches the chart, and reads all indicator values, key levels, price, and
screenshots.
"""

import json
import os
import select
import subprocess
import sys
import time

MCP_SERVER = os.path.expanduser("~/tradingview-mcp-jackson/src/server.js")
REQUEST_TIMEOUT = 30  # seconds

_mcp_proc: subprocess.Popen | None = None
_next_id = 0


def msg_id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


def send_request(method: str, params: dict | None = None) -> dict:
    """Send a JSON-RPC request and return the result dict."""
    req = {
        "jsonrpc": "2.0",
        "id": msg_id(),
        "method": method,
        "params": params or {},
    }
    line = json.dumps(req)
    _mcp_proc.stdin.write(line + "\n")
    _mcp_proc.stdin.flush()
    resp = _read_response(req["id"])
    if "error" in resp:
        err = resp["error"]
        raise RuntimeError(f"MCP error [{err.get('code')}]: {err.get('message')}")
    return resp.get("result", resp)


def send_notification(method: str, params: dict | None = None) -> None:
    """Send a JSON-RPC notification (no id, no response expected)."""
    req = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
    }
    line = json.dumps(req)
    _mcp_proc.stdin.write(line + "\n")
    _mcp_proc.stdin.flush()


def _read_response(expected_id: int) -> dict:
    """Read stdout lines until we find the matching response."""
    deadline = time.monotonic() + REQUEST_TIMEOUT
    while time.monotonic() < deadline:
        line = _mcp_proc.stdout.readline()
        if not line:
            poll = _mcp_proc.poll()
            if poll is not None:
                stderr = _mcp_proc.stderr.read()
                raise RuntimeError(
                    f"MCP server exited with code {poll}. stderr:\n{stderr}"
                )
            continue
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:
            continue
        if msg.get("id") == expected_id:
            return msg
    raise TimeoutError(
        f"No response for id={expected_id} within {REQUEST_TIMEOUT}s"
    )


def _read_any_response(timeout: float = 5.0) -> dict | None:
    """Try to read any response line, return None if none arrives."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r, _, _ = select.select([_mcp_proc.stdout], [], [], 0.5)
        if not r:
            continue
        line = _mcp_proc.stdout.readline()
        if not line:
            continue
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:
            continue
        return msg
    return None


def start_server() -> None:
    global _mcp_proc
    print(f"Starting MCP server: {MCP_SERVER}")
    _mcp_proc = subprocess.Popen(
        ["node", MCP_SERVER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    time.sleep(0.5)
    stderr_lines = []
    while True:
        r, _, _ = select.select([_mcp_proc.stderr], [], [], 0.2)
        if not r:
            break
        line = _mcp_proc.stderr.readline()
        if line:
            stderr_lines.append(line.rstrip())
        else:
            break
    for l in stderr_lines:
        print(f"  [server] {l}", file=sys.stderr)


def initialize() -> None:
    print("\n--- Initializing MCP connection ---")
    result = send_request("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "hermes", "version": "1.0"},
    })
    print(f"  Server: {result.get('serverInfo', {}).get('name')} "
          f"v{result.get('serverInfo', {}).get('version')}")
    print(f"  Protocol: {result.get('protocolVersion')}")
    send_notification("notifications/initialized")
    print("  ✅ Initialized")


def launch_tradingview() -> None:
    print("\n--- Launching TradingView with CDP ---")
    result = send_request("tools/call", {
        "name": "tv_launch",
        "arguments": {},
    })
    _print_result_lines(result)


def _print_result_lines(result: dict) -> None:
    if isinstance(result, dict):
        content = result.get("content", [])
        for item in content:
            if isinstance(item, dict) and "text" in item:
                print(f"  {item['text']}")
    else:
        print(f"  {result}")


def wait_for_health(attempts: int = 30, interval: float = 2.0) -> None:
    print("\n--- Waiting for TradingView connection ---")
    for i in range(attempts):
        time.sleep(interval)
        try:
            result = send_request("tools/call", {
                "name": "tv_health_check",
                "arguments": {},
            })
            hc = _extract_text_json(result) or result
            if isinstance(hc, dict):
                if hc.get("success", False):
                    print(f"  ✅ Connected (attempt {i+1})")
                    return
            print(f"  ⏳ Waiting... (attempt {i+1})")
        except (RuntimeError, TimeoutError) as e:
            print(f"  ⏳ Waiting... (attempt {i+1}) — {e}")
    raise TimeoutError("TradingView did not connect in time")


def get_chart_state() -> None:
    print("\n--- Getting chart state ---")
    result = send_request("tools/call", {
        "name": "chart_get_state",
        "arguments": {},
    })
    state = _extract_text_json(result) or result
    if isinstance(state, dict):
        symbol = state.get("symbol", "?")
        timeframe = state.get("timeframe", "?")
        indicators = state.get("indicators", state.get("studies", state.get("chart_state", [])))
        print(f"  Symbol: {symbol}")
        print(f"  Timeframe: {timeframe}")
        if isinstance(indicators, list) and indicators:
            print(f"  Indicators ({len(indicators)}):")
            for ind in indicators:
                name = ind.get("name", ind.get("long_name", ind.get("id", "?")))
                eid = ind.get("entity_id", ind.get("id", "?"))
                print(f"    - {name}  (entity_id: {eid})")
        else:
            print("  No indicators found")
    else:
        print(f"  {state}")


def _extract_text_json(result: dict) -> dict | list | None:
    if not isinstance(result, dict):
        return None
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and "text" in item:
                try:
                    return json.loads(item["text"])
                except (json.JSONDecodeError, TypeError):
                    pass
    return None


def _format_value(v) -> str:
    if isinstance(v, float):
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)


def get_study_values() -> None:
    print("\n--- Reading indicator values ---")
    result = send_request("tools/call", {
        "name": "data_get_study_values",
        "arguments": {},
    })
    data = _extract_text_json(result) or result
    _print_study_values(data)


def _print_study_values(data):
    if isinstance(data, dict):
        studies = data.get("studies", data.get("values", []))
        if isinstance(studies, dict):
            studies = [studies]
        for study in studies:
            name = study.get("name", study.get("study_name", "?"))
            vals = study.get("values", study.get("plots", {}))
            print(f"\n  📊 {name}:")
            _print_vals(vals)
    elif isinstance(data, list):
        for study in data:
            if isinstance(study, dict):
                name = study.get("name", study.get("study_name", "?"))
                vals = study.get("values", study.get("plots", {}))
                print(f"\n  📊 {name}:")
                _print_vals(vals)
    else:
        print(f"  {data}")


def _print_vals(vals):
    if isinstance(vals, dict):
        for k, v in vals.items():
            print(f"      {k}: {_format_value(v)}")
    elif isinstance(vals, list):
        for v in vals:
            if isinstance(v, dict):
                for k2, v2 in v.items():
                    print(f"      {k2}: {_format_value(v2)}")
            else:
                print(f"      {v}")
    else:
        print(f"      {vals}")


def get_pine_lines() -> None:
    print("\n--- Reading Pine key levels ---")
    result = send_request("tools/call", {
        "name": "data_get_pine_lines",
        "arguments": {},
    })
    data = _extract_text_json(result) or result
    if isinstance(data, dict):
        studies = data.get("studies", data.get("lines", []))
        if isinstance(studies, list) and studies:
            for study in studies:
                if isinstance(study, dict):
                    name = study.get("name", study.get("study_name", "?"))
                    levels = study.get("lines", study.get("levels", study.get("prices", [])))
                    print(f"  📏 {name}: {levels}")
            return
        print("  No Pine lines found")
    elif isinstance(data, list):
        for item in data:
            print(f"  📏 {item}")
    else:
        print(f"  line data: {data}")


def capture_screenshot() -> None:
    print("\n--- Capturing screenshot ---")
    ts = int(time.time())
    result = send_request("tools/call", {
        "name": "capture_screenshot",
        "arguments": {
            "region": "full",
            "filename": f"mcp_indicator_snapshot_{ts}",
        },
    })
    data = _extract_text_json(result) or result
    if isinstance(data, dict):
        path = data.get("path", data.get("filename", data.get("file", "")))
        print(f"  📸 Screenshot: {path}")
    else:
        _print_result_lines(result)


def get_quote() -> None:
    print("\n--- Getting current price ---")
    result = send_request("tools/call", {
        "name": "quote_get",
        "arguments": {},
    })
    data = _extract_text_json(result) or result
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                print(f"  💰 {k}:")
                for k2, v2 in v.items():
                    print(f"      {k2}: {_format_value(v2)}")
            else:
                print(f"  💰 {k}: {_format_value(v)}")
    else:
        print(f"  💰 {data}")


def stop_server() -> None:
    global _mcp_proc
    if _mcp_proc:
        _mcp_proc.terminate()
        try:
            _mcp_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _mcp_proc.kill()
            _mcp_proc.wait()
        print("\nServer stopped.")


def main():
    try:
        start_server()
        initialize()
        launch_tradingview()
        wait_for_health()
        get_chart_state()
        get_study_values()
        get_pine_lines()
        get_quote()
        capture_screenshot()
        print("\n✅ All data collected successfully")
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        raise
    finally:
        stop_server()


if __name__ == "__main__":
    main()
