"""Fixed OpenRouter reads, matching OpenPulse client safety.

Kept local so tokenserver also runs directly without package path changes.
Never surface response bodies or credentials.
"""
import json
import urllib.error
import urllib.request


class SourceError(Exception):
    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Credentials must never follow a redirect.


class Client:
    def __init__(self, opener=None):
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    def get(self, endpoint, token):
        if endpoint not in ("key", "credits"):
            raise ValueError("unsupported endpoint")
        if not token:
            raise SourceError("missing_credential")
        request = urllib.request.Request("https://openrouter.ai/api/v1/" + endpoint,
                                         headers={"Authorization": "Bearer " + token,
                                                  "Accept": "application/json",
                                                  "User-Agent": "OpenPulse/0.1"})
        try:
            with self.opener.open(request, timeout=8) as response:
                body = response.read(65537)
            if len(body) > 65536:
                raise SourceError("invalid_response")
            payload = json.loads(body)
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
                raise SourceError("invalid_response")
            return payload["data"]
        except urllib.error.HTTPError as error:
            code = "auth" if error.code in (401, 403) else "rate_limited" if error.code == 429 else "upstream"
            retry = error.headers.get('Retry-After') if error.headers else None
            status = error.code
            error.close()
            raise SourceError(code, status, retry) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise SourceError("connection") from None
        except (ValueError, UnicodeError):
            raise SourceError("invalid_response") from None
