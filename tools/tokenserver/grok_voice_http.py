"""Bounded HTTP adapter for the existing tokenserver Handler."""
import json
import re

try:
    from .grok_voice import VoiceError
except ImportError:
    from grok_voice import VoiceError


def handle_voice(handler, method):
    if not handler.path.startswith("/grokbot/"):
        return False
    service = handler.grok_voice
    try:
        if service is None:
            raise VoiceError(404, "voice_disabled")
        auth = handler.headers.get_all("Authorization", [])
        reply = handler.path == "/grokbot/reply"
        if len(auth) != 1 or not service.authorized(auth[0], reply=reply):
            raise VoiceError(401, "unauthorized")
        if handler.headers.get("Origin"):
            raise VoiceError(403, "browser_ingress_rejected")
        lengths = handler.headers.get_all("Content-Length", [])
        if handler.headers.get("Transfer-Encoding") or len(lengths) > 1:
            raise VoiceError(400, "invalid_framing")

        def body(limit, content_type):
            if handler.headers.get_content_type() != content_type:
                raise VoiceError(415, "invalid_content_type")
            try:
                length = int(lengths[0]) if lengths else 0
            except ValueError:
                raise VoiceError(400, "invalid_length") from None
            if length <= 0 or length > limit:
                raise VoiceError(413, "body_limit")
            previous = handler.connection.gettimeout()
            handler.connection.settimeout(3)
            try:
                raw = handler.rfile.read(length)
            finally:
                handler.connection.settimeout(previous)
            if len(raw) != length:
                raise VoiceError(400, "incomplete_body")
            handler._request_body_consumed = True
            if content_type == "application/json":
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    raise VoiceError(400, "invalid_json") from None
                if not isinstance(value, dict):
                    raise VoiceError(400, "invalid_json")
                return value
            return raw

        match = re.fullmatch(r"/grokbot/(session|audio|playback|played)/([0-9a-f-]{36})(?:/([0-9]+))?",
                             handler.path)
        if method == "POST" and reply:
            result = service.reply(body(16384, "application/json"))
        elif method == "POST" and handler.path == "/grokbot/session":
            result = service.begin(body(1024, "application/json").get("device"))
        elif match:
            route, sid, index = match.groups()
            if route == "session" and method == "GET" and index is None:
                result = service.snapshot(sid)
            elif route == "audio" and method == "POST" and index is not None:
                result = service.push(sid, body(6400, "application/octet-stream"), int(index))
            elif route == "played" and method == "POST" and index is None:
                body(128, "application/json")
                result = service.played(sid)
            elif route == "playback" and method == "GET" and index in ("1", "2"):
                pcm = service.playback(sid, int(index))
                handler.send_response(200)
                handler.send_header("Content-Type", "application/octet-stream")
                handler.send_header("Content-Length", str(len(pcm)))
                handler.send_header("Cache-Control", "no-store")
                handler.send_header("Connection", "close")
                handler.end_headers()
                handler.close_connection = True
                handler.wfile.write(pcm)
                return True
            else:
                raise VoiceError(404, "unknown_voice_route")
        else:
            raise VoiceError(404, "unknown_voice_route")
        handler._send(200, result)
    except VoiceError as error:
        handler._send(error.code, {"error": error.message})
    except (OSError, ValueError, TypeError, OverflowError):
        handler._send(400, {"error": "invalid_voice_request"})
    return True
