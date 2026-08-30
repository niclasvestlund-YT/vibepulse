#!/usr/bin/env python3
"""Bounded line-delimited JSON-RPC MCP bridge for VibePulse questions."""

from __future__ import annotations

import json
import os
import secrets
import sys
import unicodedata

from loopback import READ_TIMEOUT_SECONDS, post_json, strict_json_loads


METHODS = {
    "initialize", "notifications/initialized", "ping", "tools/list",
    "tools/call",
}
TOOL_NAME = "ask"
MAX_LINE_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 16 * 1024
MAX_TREE_DEPTH = 12
MAX_TREE_ITEMS = 512
LATEST_PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = {LATEST_PROTOCOL, "2024-11-05"}
_MISSING = object()
_PROCESS_SESSION_ID = secrets.token_urlsafe(18)

TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["question", "options"],
    "properties": {
        "question": {
            "type": "string", "minLength": 1,
            "description": "Non-empty question, at most 96 UTF-8 bytes.",
            "x-vibepulse-maxUtf8Bytes": 96,
        },
        "header": {
            "type": "string", "minLength": 1,
            "description": "Optional header, at most 64 UTF-8 bytes.",
            "x-vibepulse-maxUtf8Bytes": 64,
        },
        "options": {
            "type": "array", "minItems": 2, "maxItems": 3,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["label"],
                "properties": {
                    "label": {
                        "type": "string", "minLength": 1,
                        "description": "Option label, at most 64 UTF-8 bytes.",
                        "x-vibepulse-maxUtf8Bytes": 64,
                    },
                    "description": {
                        "type": "string", "minLength": 1,
                        "description": "Option detail, at most 64 UTF-8 bytes.",
                        "x-vibepulse-maxUtf8Bytes": 64,
                    },
                    "recommended": {"type": "boolean"},
                },
            },
        },
    },
}
TOOL = {
    "name": TOOL_NAME,
    "description": "Ask one short VibePulse choice question.",
    "inputSchema": TOOL_SCHEMA,
}


def _compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def _bounded_tree(value, depth=0, budget=None):
    if budget is None:
        budget = [MAX_TREE_ITEMS]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_TREE_DEPTH:
        return False
    if isinstance(value, dict):
        return all(_protocol_text(key, 128) and
                   _bounded_tree(item, depth + 1, budget)
                   for key, item in value.items())
    if isinstance(value, list):
        return all(_bounded_tree(item, depth + 1, budget) for item in value)
    if isinstance(value, str):
        return _protocol_text(value, 8192, allow_empty=True)
    return value is None or isinstance(value, (bool, int, float))


def _write(value):
    try:
        encoded = (_compact(value) + "\n").encode("utf-8")
        if len(encoded) > MAX_OUTPUT_BYTES:
            encoded = (_compact(_error(None, -32603, "Internal error")) + "\n").encode()
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    except Exception:
        pass


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _tool_error(message):
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _protocol_text(value, maximum, *, allow_empty=False):
    if not isinstance(value, str) or (not value and not allow_empty):
        return False
    try:
        if len(value.encode("utf-8")) > maximum:
            return False
    except UnicodeEncodeError:
        return False
    return not any(unicodedata.category(char).startswith("C") for char in value)


def _clean_text(value, maximum):
    if not isinstance(value, str) or not value.strip():
        return False
    return _protocol_text(value, maximum)


def _valid_arguments(arguments):
    if not isinstance(arguments, dict):
        return False
    if not set(arguments).issubset({"question", "header", "options"}):
        return False
    if set(arguments) < {"question", "options"}:
        return False
    if not _clean_text(arguments["question"], 96):
        return False
    if "header" in arguments and not _clean_text(arguments["header"], 64):
        return False
    options = arguments["options"]
    if not isinstance(options, list) or not 2 <= len(options) <= 3:
        return False
    recommended = 0
    for option in options:
        if not isinstance(option, dict) or not set(option).issubset(
                {"label", "description", "recommended"}):
            return False
        if "label" not in option or not _clean_text(option["label"], 64):
            return False
        if "description" in option and not _clean_text(option["description"], 64):
            return False
        if "recommended" in option and not isinstance(option["recommended"], bool):
            return False
        recommended += option.get("recommended") is True
    return recommended <= 1


def _port():
    raw = os.environ.get("VIBEPULSE_PORT")
    if raw is None:
        return 8737
    if not raw.isascii() or not raw.isdecimal():
        return None
    value = int(raw)
    return value if 1 <= value <= 65535 else None


def _read_timeout():
    raw = os.environ.get("_VIBEPULSE_TEST_READ_TIMEOUT")
    if raw is None:
        return READ_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return READ_TIMEOUT_SECONDS
    return value if 0 < value <= READ_TIMEOUT_SECONDS else READ_TIMEOUT_SECONDS


def _context():
    cwd = os.environ.get("VIBEPULSE_CWD")
    if not _clean_text(cwd, 1024):
        try:
            cwd = os.getcwd()
        except OSError:
            cwd = None
    if not _clean_text(cwd, 1024):
        cwd = "local-workspace"

    session_id = os.environ.get("VIBEPULSE_SESSION_ID")
    if not _clean_text(session_id, 256):
        session_id = _PROCESS_SESSION_ID

    turn_id = os.environ.get("VIBEPULSE_TURN_ID")
    if not _clean_text(turn_id, 256):
        turn_id = secrets.token_urlsafe(18)
    return {"cwd": cwd, "session_id": session_id, "turn_id": turn_id}


def _valid_server_result(value, options):
    if not isinstance(value, dict):
        return False
    if value.get("status") == "answered":
        if set(value) != {"status", "option_index", "answer"}:
            return False
        index = value["option_index"]
        recommended = [position for position, option in enumerate(options)
                       if option.get("recommended") is True]
        return type(index) is int and 0 <= index < len(options) and \
            recommended == [index] and _clean_text(value["answer"], 64) and \
            value["answer"] == options[index]["label"]
    if value.get("status") == "computer":
        return set(value) == {"status", "reason"} and \
            _clean_text(value["reason"], 128)
    return False


def _computer_fallback():
    return {
        "status": "computer",
        "reason": "unavailable",
        "instruction": "Use built-in request_user_input on this computer.",
    }


def _tool_success(payload):
    text = _compact(payload)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
    }


def _initialize(params):
    if not isinstance(params, dict):
        return None
    if set(params) != {"protocolVersion", "capabilities", "clientInfo"}:
        return None
    protocol = params.get("protocolVersion")
    if not _clean_text(protocol, 64):
        return None
    if not isinstance(params["capabilities"], dict) or \
            not _bounded_tree(params["capabilities"]):
        return None
    client = params["clientInfo"]
    if not isinstance(client, dict) or \
            not {"name", "version"}.issubset(client) or \
            not set(client).issubset({"name", "title", "version"}) or \
            not _clean_text(client["name"], 128) or \
            not _clean_text(client["version"], 64):
        return None
    if "title" in client and not _clean_text(client["title"], 128):
        return None
    selected = protocol if protocol in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
    return {
        "protocolVersion": selected,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "vibepulse", "version": "0.1.7"},
    }


def _empty_or_metadata_only(params):
    if params in (_MISSING, None, {}):
        return True
    return (isinstance(params, dict) and set(params) == {"_meta"} and
            isinstance(params["_meta"], dict) and
            _bounded_tree(params["_meta"]))


def _dispatch(method, params):
    if method == "initialize":
        result = _initialize(params)
        return result if result is not None else _MISSING
    if method in {"ping", "tools/list", "notifications/initialized"}:
        if not _empty_or_metadata_only(params):
            return _MISSING
        if method == "tools/list":
            return {"tools": [TOOL]}
        return {}
    if (not isinstance(params, dict) or
            not {"name", "arguments"}.issubset(params) or
            not set(params).issubset({"name", "arguments", "_meta"}) or
            ("_meta" in params and
             (not isinstance(params["_meta"], dict) or
              not _bounded_tree(params["_meta"])))):
        return _tool_error("Invalid ask parameters")
    if params["name"] != TOOL_NAME or not _bounded_tree(params["arguments"]) or \
            not _valid_arguments(params["arguments"]):
        return _tool_error("Invalid ask parameters")
    context = _context()
    port = _port()
    if port is None:
        return _tool_success(_computer_fallback())
    payload = dict(params["arguments"])
    payload.update(context)
    response = post_json(
        f"http://127.0.0.1:{port}/api/codex/question", payload,
        read_timeout=_read_timeout())
    if not _valid_server_result(response, params["arguments"]["options"]):
        response = _computer_fallback()
    return _tool_success(response)


def _handle(value):
    if not isinstance(value, dict):
        return _error(None, -32600, "Invalid Request")
    allowed = {"jsonrpc", "id", "method", "params"}
    if not set(value).issubset(allowed) or value.get("jsonrpc") != "2.0" or \
            not _protocol_text(value.get("method"), 128):
        return _error(None, -32600, "Invalid Request")
    has_id = "id" in value
    request_id = value["id"] if has_id else _MISSING
    if has_id and (isinstance(request_id, bool) or
            not (request_id is None or isinstance(request_id, (str, int)))):
        return _error(None, -32600, "Invalid Request")
    if isinstance(request_id, str) and not _protocol_text(
            request_id, 256, allow_empty=True):
        return _error(None, -32600, "Invalid Request")
    method = value["method"]
    params = value.get("params", _MISSING)
    if method == "notifications/initialized":
        if has_id:
            return _error(request_id, -32600, "Invalid Request")
        return None
    if not has_id:
        return None
    if method != "tools/call" and params is not _MISSING and \
            not _bounded_tree(params):
        return _error(request_id, -32602, "Invalid params")
    if method not in METHODS:
        return _error(request_id, -32601, "Method not found")
    dispatched = _dispatch(method, params)
    if dispatched is _MISSING:
        return _error(request_id, -32602, "Invalid params")
    return _result(request_id, dispatched)


def _drain_line(stream):
    while True:
        chunk = stream.readline(MAX_LINE_BYTES + 1)
        if not chunk or chunk.endswith(b"\n"):
            return


def main():
    stream = sys.stdin.buffer
    while True:
        raw = stream.readline(MAX_LINE_BYTES + 1)
        if not raw:
            break
        if len(raw) > MAX_LINE_BYTES and not raw.endswith(b"\n"):
            _drain_line(stream)
            _write(_error(None, -32600, "Invalid Request"))
            continue
        try:
            text = raw.decode("utf-8", errors="strict")
            value = strict_json_loads(text)
            response = _handle(value)
        except Exception:
            response = _error(None, -32600, "Invalid Request")
        if response is not None:
            _write(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
