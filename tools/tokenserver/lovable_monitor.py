"""Read-only Lovable workspace monitor: plan and credit balance for the glass.

The display never talks to Lovable. This module keeps OAuth, TLS, the MCP
session and retry state on the Mac that already runs the tokenserver, and
publishes one flat LAN payload on ``/api/lovable`` that carries numbers only:
plan label, credits remaining, optional workspace name and data age. The
OAuth tokens are stored in the macOS Keychain (a 0600 file elsewhere) and are
never placed in a payload, a log line or a relay.

Only one data call is ever made: the official, read-only MCP tool
``get_workspace`` on https://mcp.lovable.dev. ``list_workspaces`` is used once,
during ``login``, to find the workspace id when the user has not given one.
Both need only the ``workspaces:read`` scope; ``offline`` gives a refresh
token so the login survives restarts.

Credit grant size, reset time and a separate daily credit pool are published
only when the tool result names them explicitly. The panel can then draw its
rings/countdown and a small daily counter. Nothing is inferred from the plan:
without those fields the page shows only the main balance.

CLI (run on the Mac)::

    python3 tools/tokenserver/lovable_monitor.py login [--workspace ID]
    python3 tools/tokenserver/lovable_monitor.py status
    python3 tools/tokenserver/lovable_monitor.py probe     # key names, no values
    python3 tools/tokenserver/lovable_monitor.py logout
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import logging
import math
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path


log = logging.getLogger("tokenserver.lovable")

MCP_URL = "https://mcp.lovable.dev"
RESOURCE_METADATA_URL = MCP_URL + "/.well-known/oauth-protected-resource"
SCOPES = "workspaces:read offline"
CLIENT_NAME = "VibePulse shelf display"
MCP_PROTOCOL_VERSION = "2025-06-18"

POLL_SECONDS = 5 * 60.0
FAILURE_BACKOFF_SECONDS = 10 * 60.0
STALE_AFTER_SECONDS = 15 * 60.0
MAX_RESPONSE_BYTES = 512 * 1024
HTTP_TIMEOUT_SECONDS = 15

KEYCHAIN_SERVICE = "se.torget.vibepulse.lovable"
KEYCHAIN_ACCOUNT = "oauth"
FALLBACK_STORE = Path.home() / ".config" / "vibepulse" / "lovable-oauth.json"

# Keys are compared after lower-casing and removing "_" and "-". Order is
# preference: the first identifiable key wins.
_CREDIT_KEYS = (
    "creditsremaining", "remainingcredits", "creditbalance",
    "creditsbalance", "availablecredits", "creditsavailable",
    "credits", "balance",
)
_PLAN_KEYS = ("planname", "plan", "plantype", "tier", "subscriptionplan",
              "subscriptiontier", "name")
_PLAN_CONTAINERS = ("plan", "subscription", "billing")
_CREDIT_CONTAINERS = ("credits", "creditbalance", "balance", "billing",
                      "usage")
_DAILY_CONTAINERS = ("dailycredits", "dailycreditbalance", "dailyallowance",
                     "daily", "credits", "billing", "usage")
_DAILY_CREDIT_KEYS = (
    "dailycreditsremaining", "remainingdailycredits",
    "dailycreditbalanceremaining", "dailycreditbalance",
    "dailycreditsavailable", "availabledailycredits",
    "dailyfreecreditsremaining", "dailypromocreditsremaining",
)
_DAILY_GRANT_KEYS = (
    "dailycreditsgranted", "dailygrantedcredits", "dailytotalcredits",
    "totaldailycredits", "dailycreditlimit", "dailycreditslimit",
    "dailyallowance", "dailyincludedcredits", "dailycredits",
)
_GRANT_KEYS = (
    "totalgranted", "creditsgranted", "grantedcredits", "granted",
    "totalcredits", "creditlimit", "creditslimit", "monthlycredits",
    "includedcredits", "allowance", "total", "limit",
)
_RESET_KEYS = (
    "creditsresetat", "nextresetat", "resetsat", "resetat", "nextrefillat",
    "refillsat", "renewsat", "renewalat", "expiresat", "expiration",
    "expirationdate", "currentperiodend", "periodend", "billingperiodend",
    "usageperiodend",
)
_START_KEYS = (
    "currentperiodstart", "periodstart", "billingperiodstart",
    "usageperiodstart", "grantedat", "startsat",
)
_MAX_PERIOD_SECONDS = 400 * 86400
_WORKSPACE_NAME_KEYS = ("workspacename", "name", "displayname")
_PLAN_LABEL_RE = re.compile(r"[^A-Z0-9 +]")
_ASCII_NAME_RE = re.compile(r"[^\x20-\x7e]")


class LovableAuthError(RuntimeError):
    """Login is missing, revoked or cannot be refreshed. User action needed."""


# --------------------------------------------------------------------------
# Token store: Keychain on macOS, 0600 JSON file elsewhere.
# --------------------------------------------------------------------------

class TokenStore:
    def __init__(self, *, runner=None, path=None, platform=None):
        self._run = runner or subprocess.run
        self._path = Path(path) if path else FALLBACK_STORE
        self._mac = (platform or sys.platform) == "darwin"

    def load(self):
        if self._mac:
            result = self._run(
                ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                 "-a", KEYCHAIN_ACCOUNT, "-w"],
                capture_output=True, text=True, check=False)
            if result.returncode != 0 or not result.stdout.strip():
                return None
            raw = result.stdout.strip()
        else:
            try:
                raw = self._path.read_text()
            except FileNotFoundError:
                return None
        try:
            data = json.loads(raw)
        except ValueError:
            log.warning("the saved Lovable login is unreadable; run login again")
            return None
        return data if isinstance(data, dict) else None

    def save(self, data):
        raw = json.dumps(data, separators=(",", ":"))
        if self._mac:
            # -U updates in place. The secret is passed as an argument to the
            # system tool only; it is never logged or written to disk by us.
            result = self._run(
                ["security", "add-generic-password", "-U", "-s",
                 KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w", raw],
                capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RuntimeError("could not save the Lovable login in Keychain")
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(raw)
        os.replace(tmp, self._path)

    def clear(self):
        if self._mac:
            self._run(["security", "delete-generic-password", "-s",
                       KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
                      capture_output=True, text=True, check=False)
        else:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def _read_limited(response):
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Lovable response exceeded 512 KiB")
    return raw


def _post_form(opener, url, fields):
    body = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"})
    with opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(_read_limited(response))


def _get_json(opener, url):
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(_read_limited(response))


def _post_json(opener, url, payload):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json"})
    with opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(_read_limited(response))


def discover(opener=None):
    """Return the authorization server metadata via RFC 9728 + RFC 8414."""
    opener = opener or urllib.request.urlopen
    resource = _get_json(opener, RESOURCE_METADATA_URL)
    servers = resource.get("authorization_servers") or []
    if not servers or not isinstance(servers[0], str):
        raise RuntimeError("Lovable MCP advertised no authorization server")
    issuer = servers[0].rstrip("/")
    parsed = urllib.parse.urlsplit(issuer)
    if parsed.scheme != "https":
        raise RuntimeError("Lovable authorization server is not https")
    well_known = (f"{parsed.scheme}://{parsed.netloc}"
                  f"/.well-known/oauth-authorization-server{parsed.path}")
    meta = _get_json(opener, well_known)
    for key in ("authorization_endpoint", "token_endpoint"):
        if not str(meta.get(key, "")).startswith("https://"):
            raise RuntimeError(f"Lovable metadata lacks an https {key}")
    return meta


# --------------------------------------------------------------------------
# OAuth: dynamic registration + authorization code with PKCE, loopback redirect
# --------------------------------------------------------------------------

def _pkce_pair():
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result = None

    def do_GET(self):  # noqa: N802 - http.server API
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        type(self).result = {k: v[0] for k, v in query.items() if v}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in type(self).result
        self.wfile.write((
            "<html><body style='font-family:system-ui;background:#000;"
            "color:#fff;text-align:center;padding-top:20vh'><h1>"
            + ("VibePulse is connected to Lovable" if ok else
               "Lovable login was not completed")
            + "</h1><p>You can close this tab.</p></body></html>").encode())

    def log_message(self, *args):  # silence default stderr logging
        return


def login(store=None, *, workspace_id=None, opener=None, open_browser=None,
          timeout=300):
    """Interactive one-time login. Returns the saved record (no secrets shown)."""
    opener = opener or urllib.request.urlopen
    store = store or TokenStore()
    meta = discover(opener)

    server = http.server.HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    registration = _post_json(opener, meta["registration_endpoint"], {
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": SCOPES,
    })
    client_id = registration.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise RuntimeError("Lovable did not register the VibePulse client")

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    url = meta["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": MCP_URL,
    })
    print("Opening Lovable in your browser to connect VibePulse (read-only)…")
    print("If nothing opens, visit:\n  " + url)
    (open_browser or webbrowser.open)(url)

    _CallbackHandler.result = None
    server.timeout = 1
    deadline = time.monotonic() + timeout
    try:
        while _CallbackHandler.result is None and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    result = _CallbackHandler.result or {}
    if result.get("state") != state:
        raise LovableAuthError("Lovable login failed: state mismatch or timeout")
    if "code" not in result:
        raise LovableAuthError(
            "Lovable login was declined: " + result.get("error", "no code"))

    tokens = _post_form(opener, meta["token_endpoint"], {
        "grant_type": "authorization_code",
        "code": result["code"],
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": MCP_URL,
    })
    record = _record_from_tokens({}, tokens, client_id=client_id,
                                 token_endpoint=meta["token_endpoint"])
    if workspace_id:
        record["workspace_id"] = workspace_id
    else:
        client = McpClient(record["access_token"], opener=opener)
        record["workspace_id"] = client.first_workspace_id()
    store.save(record)
    return record


def _record_from_tokens(previous, tokens, *, client_id, token_endpoint):
    access = tokens.get("access_token")
    if not isinstance(access, str) or not access:
        raise LovableAuthError("Lovable returned no access token")
    record = dict(previous)
    record.update({
        "client_id": client_id,
        "token_endpoint": token_endpoint,
        "access_token": access,
        "expires_at": time.time() + float(tokens.get("expires_in") or 3600),
    })
    if isinstance(tokens.get("refresh_token"), str):
        record["refresh_token"] = tokens["refresh_token"]
    return record


def refresh(record, opener=None):
    opener = opener or urllib.request.urlopen
    token = record.get("refresh_token")
    if not token:
        raise LovableAuthError("Lovable login expired; run login again")
    try:
        tokens = _post_form(opener, record["token_endpoint"], {
            "grant_type": "refresh_token",
            "refresh_token": token,
            "client_id": record["client_id"],
            "resource": MCP_URL,
        })
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401):
            raise LovableAuthError("Lovable login was revoked; run login again") from None
        raise
    return _record_from_tokens(record, tokens,
                               client_id=record["client_id"],
                               token_endpoint=record["token_endpoint"])


# --------------------------------------------------------------------------
# Minimal MCP Streamable HTTP client: initialize, then tools/call.
# --------------------------------------------------------------------------

class McpClient:
    def __init__(self, access_token, *, opener=None):
        self._token = access_token
        self._opener = opener or urllib.request.urlopen
        self._session = None
        self._id = 0

    def _rpc(self, method, params=None, *, notify=False):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            self._id += 1
            payload["id"] = self._id
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._token}",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        request = urllib.request.Request(
            MCP_URL, data=json.dumps(payload).encode(), method="POST",
            headers=headers)
        try:
            with self._opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                session = response.headers.get("Mcp-Session-Id") \
                    if getattr(response, "headers", None) else None
                if session:
                    self._session = session
                raw = _read_limited(response)
                ctype = (response.headers.get("Content-Type", "")
                         if getattr(response, "headers", None) else "")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise LovableAuthError(f"Lovable MCP answered HTTP {exc.code}") from None
            raise
        if notify:
            return None
        message = _parse_rpc_body(raw, ctype, self._id)
        if "error" in message:
            err = message["error"] or {}
            raise RuntimeError(f"Lovable MCP error: {err.get('message', err)}")
        return message.get("result")

    def _initialize(self):
        if self._session is not None or self._id:
            return
        self._rpc("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "vibepulse-tokenserver", "version": "1"},
        })
        self._rpc("notifications/initialized", notify=True)

    def call_tool(self, name, arguments):
        self._initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            raise ValueError("Lovable MCP tool result was not an object")
        if result.get("isError"):
            raise RuntimeError(f"Lovable tool {name} reported an error")
        return tool_result_json(result)

    def get_workspace(self, workspace_id):
        return self.call_tool("get_workspace", {"workspace_id": workspace_id})

    def first_workspace_id(self):
        data = self.call_tool("list_workspaces", {})
        items = data
        if isinstance(data, dict):
            for key in ("workspaces", "items", "data", "results"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    wid = item.get("id") or item.get("workspace_id")
                    if isinstance(wid, str) and wid:
                        return wid
        raise RuntimeError("no Lovable workspace found; pass --workspace ID")


def _parse_rpc_body(raw, content_type, want_id):
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" in content_type:
        for block in text.split("\n\n"):
            data = "".join(line[5:].lstrip() for line in block.splitlines()
                           if line.startswith("data:"))
            if not data:
                continue
            message = json.loads(data)
            if isinstance(message, dict) and message.get("id") == want_id:
                return message
        raise ValueError("Lovable MCP stream carried no response")
    message = json.loads(text)
    if not isinstance(message, dict):
        raise ValueError("Lovable MCP response was not an object")
    return message


def tool_result_json(result):
    """The tool payload as JSON: structuredContent, else the first JSON text."""
    structured = result.get("structuredContent")
    if isinstance(structured, (dict, list)):
        return structured
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                return json.loads(item.get("text") or "")
            except ValueError:
                continue
    raise ValueError("Lovable tool result carried no JSON")


# --------------------------------------------------------------------------
# Extraction: identify plan and credit balance, never infer them.
# --------------------------------------------------------------------------

def _norm(key):
    return str(key).lower().replace("_", "").replace("-", "")


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) and parsed >= 0 else None
    return None


def _find(obj, keys, accept, containers=(), depth=0):
    """Depth-limited search; prefers shallower matches and key order."""
    if depth > 4 or not isinstance(obj, dict):
        return None
    normalized = {_norm(k): v for k, v in obj.items()}
    for key in keys:
        if key in normalized:
            found = accept(normalized[key])
            if found is not None:
                return found
    for key in containers:
        child = normalized.get(key)
        if isinstance(child, dict):
            found = _find(child, keys, accept, containers, depth + 1)
            if found is not None:
                return found
    for value in obj.values():
        if isinstance(value, dict):
            found = _find(value, keys, accept, containers, depth + 1)
            if found is not None:
                return found
    return None


def _plan_label(value):
    if not isinstance(value, str):
        return None
    label = _PLAN_LABEL_RE.sub("", value.strip().upper())[:16].strip()
    return label or None


def _ascii_name(value):
    if not isinstance(value, str):
        return None
    name = _ASCII_NAME_RE.sub("", value).strip()[:40].strip()
    return name or None


def _timestamp(value):
    """ISO-8601 string or epoch seconds/milliseconds -> epoch seconds."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
        return float(value) / 1000.0 if value > 1e11 else float(value)
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def extract(workspace):
    """Return {"plan", "credits", "workspace"} with None for unknown fields."""
    root = workspace
    if isinstance(root, dict) and isinstance(root.get("workspace"), dict):
        root = root["workspace"]
    if not isinstance(root, dict):
        raise ValueError("get_workspace did not return an object")

    credits = _find(root, _CREDIT_KEYS, _number, _CREDIT_CONTAINERS)
    if credits is None:
        # A nested credits object like {"remaining": 12, "total": 100}.
        for key in _CREDIT_CONTAINERS:
            child = {_norm(k): v for k, v in root.items()}.get(key)
            if isinstance(child, dict):
                credits = _find(child, ("remaining", "available", "current"),
                                _number)
                if credits is not None:
                    break

    plan = None
    normalized = {_norm(k): v for k, v in root.items()}
    for key in _PLAN_KEYS[:-1]:
        plan = _plan_label(normalized.get(key))
        if plan:
            break
    if plan is None:
        for key in _PLAN_CONTAINERS:
            child = normalized.get(key)
            if isinstance(child, dict):
                plan = _find(child, _PLAN_KEYS, _plan_label)
                if plan:
                    break

    name = None
    for key in _WORKSPACE_NAME_KEYS:
        name = _ascii_name(normalized.get(key))
        if name:
            break

    # Keep daily pools out of the main allowance search. _find deliberately
    # walks nested objects, so an explicit daily allowance must not become the
    # monthly/main grant merely because both use a key such as "allowance".
    main_root = {key: value for key, value in root.items()
                 if _norm(key) not in _DAILY_CONTAINERS[:4]}
    grant = _find(main_root, _GRANT_KEYS, _number, _CREDIT_CONTAINERS)
    if grant is not None and grant <= 0:
        grant = None
    reset_at = _find(root, _RESET_KEYS, _timestamp, _CREDIT_CONTAINERS)
    start_at = _find(root, _START_KEYS, _timestamp, _CREDIT_CONTAINERS)

    # Daily credits are a distinct pool. Never derive them from the plan name
    # (Pro happens to advertise five today, but other plans can differ). A
    # remaining/balance field and an allowance/limit field keep their meaning
    # separate all the way to the glass.
    daily_credits = _find(root, _DAILY_CREDIT_KEYS, _number,
                          _DAILY_CONTAINERS)
    if daily_credits is None:
        normalized_root = {_norm(k): v for k, v in root.items()}
        for key in _DAILY_CONTAINERS[:4]:
            child = normalized_root.get(key)
            if isinstance(child, dict):
                daily_credits = _find(
                    child, ("remaining", "available", "current", "balance"),
                    _number)
                if daily_credits is not None:
                    break
    daily_grant = _find(root, _DAILY_GRANT_KEYS, _number, _DAILY_CONTAINERS)
    if daily_grant is None:
        normalized_root = {_norm(k): v for k, v in root.items()}
        for key in _DAILY_CONTAINERS[:4]:
            child = normalized_root.get(key)
            if isinstance(child, dict):
                daily_grant = _find(child, _GRANT_KEYS, _number)
                if daily_grant is not None:
                    break
    if daily_grant is not None and daily_grant <= 0:
        daily_grant = None

    return {"plan": plan, "credits": credits, "workspace": name,
            "grant": grant, "reset_at": reset_at, "start_at": start_at,
            "daily_credits": daily_credits, "daily_grant": daily_grant}


def describe_shape(obj, prefix="", depth=0, out=None):
    """Key paths and value types only -- safe to print, carries no values."""
    out = [] if out is None else out
    if depth > 5:
        return out
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.append(f"{path}: {type(value).__name__}")
            describe_shape(value, path, depth + 1, out)
    elif isinstance(obj, list) and obj:
        describe_shape(obj[0], prefix + "[0]", depth + 1, out)
    return out


# --------------------------------------------------------------------------
# The monitor thread used by tokenserver
# --------------------------------------------------------------------------

class LovableMonitor:
    """Poll get_workspace without ever raising into tokenserver."""

    def __init__(self, *, store=None, opener=None, clock=None,
                 poll_seconds=POLL_SECONDS, client_factory=None,
                 wall_clock=None):
        self._wall = wall_clock or time.time
        self._store = store or TokenStore()
        self._opener = opener or urllib.request.urlopen
        self._clock = clock or time.monotonic
        self._client_factory = client_factory or (
            lambda token: McpClient(token, opener=self._opener))
        self.poll_seconds = float(poll_seconds)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._data = None
        self._last_success_at = None
        self._last_error = None
        self._needs_login = False
        self._next_poll_at = 0.0

    def _access_token(self):
        record = self._store.load()
        if not record or not record.get("access_token"):
            raise LovableAuthError("not logged in to Lovable")
        if not record.get("workspace_id"):
            raise LovableAuthError("no Lovable workspace saved; run login")
        if float(record.get("expires_at") or 0) - time.time() < 60:
            record = refresh(record, self._opener)
            self._store.save(record)
        return record

    def poll_once(self):
        now = self._clock()
        try:
            record = self._access_token()
            try:
                workspace = self._client_factory(
                    record["access_token"]).get_workspace(record["workspace_id"])
            except LovableAuthError:
                # One forced refresh: the server may have revoked early.
                record = refresh(record, self._opener)
                self._store.save(record)
                workspace = self._client_factory(
                    record["access_token"]).get_workspace(record["workspace_id"])
            data = extract(workspace)
            if data["credits"] is None:
                raise ValueError("get_workspace carried no identifiable credit "
                                 "balance (run: lovable_monitor.py probe)")
            with self._lock:
                self._data = data
                self._last_success_at = now
                self._last_error = None
                self._needs_login = False
                self._next_poll_at = now + self.poll_seconds
            return True
        except Exception as exc:
            auth = isinstance(exc, LovableAuthError)
            with self._lock:
                self._last_error = type(exc).__name__
                self._needs_login = auth
                self._next_poll_at = now + (
                    self.poll_seconds if auth else FAILURE_BACKOFF_SECONDS)
            # Message only: exceptions here never carry tokens.
            log.warning("Lovable poll failed: %s", exc)
            return False

    def snapshot(self):
        now = self._clock()
        with self._lock:
            data = dict(self._data) if self._data else None
            last_success = self._last_success_at
            last_error = self._last_error
            needs_login = self._needs_login
        result = {
            "v": 1,
            "enabled": True,
            "stale": (last_success is None or last_error is not None or
                      now - last_success > STALE_AFTER_SECONDS),
        }
        if needs_login:
            result["login"] = True
        if data and last_success is not None:
            credits = data["credits"]
            # Tenths: exact for Lovable's half-credit steps, integer on the wire.
            result["creditsTenths"] = int(min(round(credits * 10), 2_147_483_647))
            result["ageSeconds"] = int(max(0, min(now - last_success,
                                                  2_147_483_647)))
            if data.get("plan"):
                result["plan"] = data["plan"]
            if data.get("workspace"):
                result["workspace"] = data["workspace"]
            daily_credits = data.get("daily_credits")
            if daily_credits is not None:
                result["dailyCreditsTenths"] = int(min(
                    round(daily_credits * 10), 2_147_483_647))
            daily_grant = data.get("daily_grant")
            if daily_grant:
                result["dailyGrantTenths"] = int(min(
                    round(daily_grant * 10), 2_147_483_647))
            grant = data.get("grant")
            if grant:
                result["grantTenths"] = int(min(round(grant * 10),
                                                2_147_483_647))
            reset_at = data.get("reset_at")
            if reset_at:
                left = reset_at - self._wall()
                if 0 < left <= _MAX_PERIOD_SECONDS:
                    result["resetSeconds"] = int(left)
                    start_at = data.get("start_at")
                    period = (reset_at - start_at) if start_at else 0
                    if 3600 <= period <= _MAX_PERIOD_SECONDS and period >= left:
                        result["periodSeconds"] = int(period)
        return result

    def run(self):
        while not self._stop.is_set():
            with self._lock:
                delay = max(0.0, self._next_poll_at - self._clock())
            if self._stop.wait(delay):
                break
            self.poll_once()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run, name="lovable-monitor", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)


def disabled_snapshot():
    return {"v": 1, "enabled": False}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Connect VibePulse to Lovable (read-only plan + credits).")
    commands = parser.add_subparsers(dest="command", required=True)
    login_cmd = commands.add_parser("login", help="one-time browser login")
    login_cmd.add_argument("--workspace", default=None,
                           help="workspace id (default: your first workspace)")
    commands.add_parser("status", help="fetch once and print what the glass gets")
    commands.add_parser("probe", help="print get_workspace key names (no values)")
    commands.add_parser("logout", help="forget the saved Lovable login")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    store = TokenStore()

    if args.command == "login":
        record = login(store, workspace_id=args.workspace)
        print(f"Connected. Workspace {record['workspace_id']} saved "
              f"({'Keychain' if sys.platform == 'darwin' else FALLBACK_STORE}).")
        print("Start the tokenserver with --lovable (or VIBEPULSE_LOVABLE=1).")
        return 0
    if args.command == "logout":
        store.clear()
        print("Lovable login removed from this computer.")
        return 0
    monitor = LovableMonitor(store=store)
    if args.command == "status":
        ok = monitor.poll_once()
        print(json.dumps(monitor.snapshot(), indent=2))
        return 0 if ok else 1
    if args.command == "probe":
        record = monitor._access_token()
        workspace = McpClient(record["access_token"]).get_workspace(
            record["workspace_id"])
        print("\n".join(describe_shape(workspace)))
        print("\nidentified:", {k: (v is not None)
                                for k, v in extract(workspace).items()})
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
