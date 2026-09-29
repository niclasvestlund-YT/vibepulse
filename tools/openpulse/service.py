"""Foreground OpenPulse service. Pollers never run inside HTTP request locks."""
import argparse
import copy
import getpass
import json
import ipaddress
import sys
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import credentials
from .client import Client, SourceError
from .model import key_view, normalize_credits, normalize_key, number

CONFIG_PATH = Path.home() / "Library/Application Support/OpenPulse/config.json"
EXAMPLE = Path(__file__).with_name("config.example.json")
DEMO_KEY = {"usage_daily": 1.84, "usage_weekly": 12.36, "usage_monthly": 38.42,
            "usage": 138.42, "limit": 100, "limit_remaining": 61.58,
            "limit_reset": "monthly", "include_byok_in_limit": False,
            "byok_usage_daily": 0, "byok_usage_weekly": 0, "byok_usage_monthly": 0}


def read_config(path):
    try:
        value = json.loads(Path(path).read_text())
        if not isinstance(value, dict) or set(value) - {"keys", "account_view", "management_credential_id"}:
            raise ValueError
        keys = value["keys"]
        if not isinstance(keys, list) or not 1 <= len(keys) <= 8:
            raise ValueError
        ids = set()
        for key in keys:
            required = {"id", "name", "monthly_budget_usd", "warning_at", "critical_at"}
            if not isinstance(key, dict) or not required <= set(key) or set(key) - required - {"credential_id"}:
                raise ValueError
            if not credentials.valid_id(key["id"]) or key["id"] == "management" or key["id"] in ids:
                raise ValueError
            ids.add(key["id"])
            if "credential_id" in key and not credentials.valid_id(key["credential_id"]):
                raise ValueError
            if not isinstance(key["name"], str) or not re.fullmatch(r"[A-Za-z0-9 _.-]{1,24}", key["name"]):
                raise ValueError
            budget = key["monthly_budget_usd"]
            if budget is not None and (number(budget) is None or not 0.01 <= budget <= 1e9):
                raise ValueError
            warning, critical = number(key["warning_at"]), number(key["critical_at"])
            if warning is None or critical is None or not 0 < warning < critical <= 1:
                raise ValueError
        if not isinstance(value.get("account_view", False), bool):
            raise ValueError
        if "management_credential_id" in value and not credentials.valid_id(value["management_credential_id"]):
            raise ValueError
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Invalid OpenPulse config; use config.example.json (no secrets).") from None


class Monitor:
    def __init__(self, config, *, demo=False, client=None, loader=None, clock=None, monotonic=None):
        self.config, self.demo = config, demo
        self.client, self.loader = client or Client(), loader or credentials.load
        self.clock, self.monotonic = clock or time.time, monotonic or time.monotonic
        self.lock = threading.Lock()
        self.setup_lock = threading.Lock()
        self.slots = {key["id"]: self.empty() for key in config["keys"]}
        self.slots["management"] = self.empty()
        self.stop = threading.Event()
        self.generation = 0

    @staticmethod
    def empty():
        return dict(values=None, observed=None, mono=None, error=None, failures=0, due=0)

    def refresh(self, account):
        with self.lock:
            generation = self.generation
            if account == "management":
                credential_id = self.config.get("management_credential_id", "management")
            else:
                key = next((key for key in self.config["keys"] if key["id"] == account), None)
                if key is None:
                    return
                credential_id = key.get("credential_id", account)
        management = account == "management"
        try:
            if self.demo:
                raw = {"total_credits": 200, "total_usage": 138.42} if management else DEMO_KEY
            else:
                raw = self.client.get("credits" if management else "key", self.loader(credential_id))
            values = normalize_credits(raw) if management else normalize_key(raw)
            error = None
        except SourceError as exc:
            values, error = None, str(exc)
        except Exception:
            # Deliberate secret boundary: never interpolate provider/library exceptions.
            values, error = None, "credential_or_source_error"
        with self.lock:
            if generation != self.generation:
                return
            slot = self.slots[account]
            now, mono = self.clock(), self.monotonic()
            if error is None:
                slot.update(values=values, observed=now, mono=mono, error=None, failures=0, due=mono + 60)
            else:
                failures = min(slot["failures"] + 1, 5)
                slot.update(error=error, failures=failures, due=mono + min(600, 30 * 2 ** (failures - 1)))

    def run(self):
        while not self.stop.is_set():
            with self.lock:
                accounts = [key["id"] for key in self.config["keys"]]
                if self.config.get("account_view"):
                    accounts.append("management")
            for account in accounts:
                if self.monotonic() >= self.slots[account]["due"]:
                    self.refresh(account)
            self.stop.wait(1)

    def snapshot(self, index=0):
        with self.lock:
            configuration = self.config
            config = configuration["keys"][index]
            key, account = copy.deepcopy((self.slots[config["id"]], self.slots["management"]))
        now, mono = self.clock(), self.monotonic()
        age = max(0, mono - key["mono"]) if key["mono"] is not None else None
        # Wall-clock jump/sleep may advance beyond monotonic on some hosts.
        if age is not None:
            age = max(age, now - key["observed"])
        result = key_view(key["values"], key["observed"], now, config, key["error"], age)
        result.update(demo=self.demo, keyIndex=index, keyCount=len(configuration["keys"]))
        enabled = configuration.get("account_view", False)
        account_age = max(0, mono - account["mono"], now - account["observed"]) if account["mono"] is not None else None
        result.update(accountEnabled=enabled,
                      accountBalance=(account["values"] or {}).get("accountBalance") if enabled else None,
                      accountState=("disabled" if not enabled else "error" if account["error"] else
                                    "no_data" if account_age is None or (account["values"] or {}).get("accountBalance") is None else "stale" if account_age >= 180 else "fresh"),
                      accountAgeSeconds=account_age, accountError=account["error"] if enabled else None)
        return result


def save_connection(monitor, path, token, management, name, budget):
    """Stage new immutable Keychain entries; publish their references last.

    Existing entries are never overwritten. A denied Keychain write or failed
    config replace leaves every running service on its previous credentials.
    Keep previous entries for other processes until they reload the config.
    """
    with monitor.setup_lock:
        with monitor.lock:
            config = copy.deepcopy(monitor.config)
        config["keys"][0].update(name=name, monthly_budget_usd=budget)
        config["account_view"] = bool(management)
        key_ref = "key_" + secrets.token_hex(8)
        config["keys"][0]["credential_id"] = key_ref
        config.pop("management_credential_id", None)
        writes = [(key_ref, token)]
        if management:
            management_ref = "mgmt_" + secrets.token_hex(8)
            config["management_credential_id"] = management_ref
            writes.append((management_ref, management))
        slots = {key["id"]: monitor.empty() for key in config["keys"]}
        slots["management"] = monitor.empty()
        staged, temp = [], None
        try:
            for account, secret in writes:
                staged.append(account)
                credentials.keychain(account, secret)
            path = Path(path)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
            with os.fdopen(fd, "w") as stream:
                json.dump(config, stream)
            os.replace(temp, path)
        except Exception:
            # Cleanup may be denied too; an orphan stays inactive in Keychain.
            for account in staged:
                try:
                    credentials.keychain(account, remove=True)
                except Exception:  # noqa: S110 - never log credential-bearing errors
                    pass
            if temp is not None:
                try:
                    Path(temp).unlink(missing_ok=True)
                except OSError:
                    pass
            raise RuntimeError("connection_not_saved") from None
        with monitor.lock:
            monitor.generation += 1
            monitor.config = config
            monitor.slots = slots


def make_server(monitor, host, port, *, connect_path=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return  # No request paths, headers, bodies or credentials in logs.

        def do_POST(self):
            # Setup is explicit, loopback-only, same-origin and absent in demo mode.
            expected = f"127.0.0.1:{self.server.server_port}"
            if (connect_path is None or monitor.demo or self.path != "/connect" or
                    self.client_address[0] != "127.0.0.1" or
                    self.headers.get("Host") != expected or
                    self.headers.get("Origin") != "http://" + expected or
                    self.headers.get("Content-Type") != "application/x-www-form-urlencoded"):
                self.send_error(403, "Local setup only")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise ValueError
                self.connection.settimeout(10)
                fields = parse_qs(self.rfile.read(length).decode(), strict_parsing=True)
                token = fields.get("api_key", [""])[0].strip()
                management = fields.get("management_key", [""])[0].strip()
                name = fields.get("name", [""])[0]
                budget = float(fields.get("budget", [""])[0])
                if (not token or len(token) > 512 or any(c.isspace() for c in token) or
                        len(management) > 512 or any(c.isspace() for c in management) or
                        not re.fullmatch(r"[A-Za-z0-9 _.-]{1,24}", name) or
                        number(budget) is None or not 0.01 <= budget <= 1e9 or len(monitor.config["keys"]) != 1):
                    raise ValueError
                save_connection(monitor, connect_path, token, management, name, budget)
            except Exception:
                self.send_error(400, "Unable to save connection")
                return
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def do_GET(self):
            # Block public Host / cross-origin reads, including DNS rebinding.
            authority = self.headers.get("Host", "")
            try:
                hostname = urlsplit("http://" + authority).hostname or ""
                allowed = hostname == "localhost" or hostname.endswith(".local")
                if not allowed:
                    address = ipaddress.ip_address(hostname)
                    allowed = address.is_private or address.is_loopback
            except ValueError:
                allowed = False
            origin = self.headers.get("Origin")
            if not allowed or (origin and origin != "http://" + authority):
                self.send_error(403, "Local access only")
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/connect" and connect_path is not None and not monitor.demo and self.client_address[0] == "127.0.0.1":
                body = Path(__file__).with_name("connect.html").read_bytes()
                mime = "text/html; charset=utf-8"
            elif parsed.path == "/api/openpulse":
                try:
                    index = int(parse_qs(parsed.query).get("key", ["0"])[0])
                    if not 0 <= index < len(monitor.config["keys"]):
                        raise ValueError
                    body = json.dumps(monitor.snapshot(index), allow_nan=False).encode()
                except (ValueError, IndexError):
                    self.send_error(400, "Invalid key index")
                    return
                mime = "application/json"
            elif parsed.path == "/":
                body = Path(__file__).with_name("index.html").read_bytes()
                mime = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return
    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description="OpenPulse — isolated OpenRouter spend service")
    parser.add_argument("--demo", action="store_true", help="No credentials read; synthetic values only")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8738)
    parser.add_argument("--connect", action="store_true", help="Enable loopback-only setup page (live mode)")
    parser.add_argument("--save-key", metavar="ID", help="Store a key securely in OpenPulse's Mac Keychain namespace")
    args = parser.parse_args()
    if args.save_key:
        if not sys.stdin.isatty():
            parser.error("Save keys in an interactive terminal with hidden input")
        if not credentials.valid_id(args.save_key):
            parser.error("Key id must be a short lowercase identifier")
        value = getpass.getpass("OpenRouter key (hidden): ").strip()
        if not value or any(c.isspace() for c in value):
            parser.error("Empty or invalid key")
        credentials.keychain(args.save_key, value)
        print("Saved in OpenPulse Keychain.")
        return
    if args.connect and (args.demo or args.host != "127.0.0.1"):
        parser.error("--connect requires live mode on 127.0.0.1")
    config = read_config(EXAMPLE if (args.demo and args.config == CONFIG_PATH) or
                         (args.connect and not args.config.exists()) else args.config)
    if args.demo:
        config["account_view"] = True
    monitor = Monitor(config, demo=args.demo)
    server = make_server(monitor, args.host, args.port, connect_path=args.config if args.connect else None)
    thread = threading.Thread(target=monitor.run, name="openpulse-poller", daemon=True)
    thread.start()
    print(f"OpenPulse {'DEMO' if args.demo else 'OpenRouter'}: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("OpenPulse stopped.")
    finally:
        monitor.stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
