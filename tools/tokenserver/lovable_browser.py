"""Opt-in, loopback-only ingress for visible Lovable browser balances.

No browser credentials enter this module. Configuration pins a workspace name
and an extension ID; unknown fields and stale/replayed observations are rejected.
The browser cache has its own age and cannot be made stale by an MCP failure.
"""
import json
import math
import os
import re
import threading
import time

if __package__:
    from .state_files import state_dir, fsync_parent, quarantine_corrupt
else:
    from state_files import state_dir, fsync_parent, quarantine_corrupt


class LovableBrowserBridge:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = None
        self._cache_writable = True
        self.extension_id = None
        self.workspace = None
        self._cache = state_dir() / 'lovable-browser-cache.json'
        config = state_dir() / 'lovable-browser.json'
        try:
            conf = json.loads(config.read_text(encoding="utf-8"))
            if (not isinstance(conf, dict) or
                    not re.fullmatch(r'[a-p]{32}', conf.get('extensionId', '')) or
                    not isinstance(conf.get('workspace'), str) or
                    not 1 <= len(conf['workspace']) <= 200):
                return
            self.extension_id = conf['extensionId']
            self.workspace = conf['workspace']
        except (OSError, ValueError, TypeError):
            return
        try:
            cached = json.loads(self._cache.read_text(encoding="utf-8"))
            self._validate(cached, live=False)
            self._data = cached
        except FileNotFoundError:
            pass  # noqa: S110 - No first reading has arrived yet.
        except (ValueError, TypeError):
            self._cache_writable = quarantine_corrupt(
                self._cache, 'invalid browser credit snapshot') is not None
        except OSError:
            self._cache_writable = False  # Preserve unreadable evidence.

    def _validate(self, data, *, live):
        required = {'v', 'workspace', 'credits', 'observedAt'}
        optional = {'plan', 'monthlyCredits', 'dailyBuildCredits'}
        if (not isinstance(data, dict) or not required <= data.keys() or
                data.keys() - required - optional or type(data['v']) is not int or
                data['v'] != 1 or data['workspace'] != self.workspace):
            raise ValueError('workspace or schema mismatch')
        for key in ('credits', 'monthlyCredits', 'dailyBuildCredits'):
            if key in data and (type(data[key]) not in (int, float) or
                    not math.isfinite(data[key]) or not 0 <= data[key] <= 1e7):
                raise ValueError('invalid credit number')
        stamp = data['observedAt']
        if (type(stamp) not in (int, float) or not math.isfinite(stamp) or
                stamp <= 0 or stamp > time.time() + 5 or
                (live and time.time() - stamp > 120)):
            raise ValueError('expired observation')
        if 'plan' in data and data['plan'] not in ('FREE', 'PRO', 'BUSINESS', 'ENTERPRISE'):
            raise ValueError('invalid plan')

    def accept(self, data):
        if self.extension_id is None:
            raise ValueError('browser bridge is not configured')
        if not self._cache_writable:
            raise ValueError('browser cache is unreadable; repair it before receiving updates')
        self._validate(data, live=True)
        with self._lock:
            if self._data and data['observedAt'] <= self._data['observedAt']:
                raise ValueError('old observation')
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache.with_suffix('.tmp')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(data, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._cache)
            self._data = dict(data)
            fsync_parent(self._cache)

    def snapshot(self, official):
        with self._lock:
            data = dict(self._data) if self._data else None
        if data is None:
            return official
        age = int(max(0, min(time.time() - data['observedAt'], 2147483647)))
        if ('creditsTenths' in official and not official.get('stale', True) and
                (age > 180 or official.get('ageSeconds', 2147483647) <= age)):
            return official
        result = {'v': 1, 'enabled': True, 'stale': age > 180,
                  'creditsTenths': round(data['credits'] * 10), 'ageSeconds': age}
        if data.get('plan'):
            result['plan'] = data['plan']
        # Monthly allowance is explicitly labelled; never use a graph's maximum.
        if data.get('monthlyCredits', 0) > 0:
            result['grantTenths'] = round(data['monthlyCredits'] * 10)
        if 'dailyBuildCredits' in data:
            result['dailyCreditsTenths'] = round(data['dailyBuildCredits'] * 10)
        # No workspace, chat percentage, guessed date/year or expiry->reset mapping.
        return result
