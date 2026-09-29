"""Only fixed, read-only OpenRouter endpoints. Never surface response bodies."""
import json
import urllib.error
import urllib.request


class SourceError(Exception):
    pass


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
            error.close()
            raise SourceError(code) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise SourceError("connection") from None
        except (ValueError, UnicodeError):
            raise SourceError("invalid_response") from None
