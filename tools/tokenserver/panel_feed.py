"""Bounded, cached GitHub cards for the loopback browser bridge.

Failures deliberately discard exception details: API bodies and credentials
must never reach either the display or logs.
"""
import json
import os
import re
import threading
import time
import urllib.error
from datetime import datetime, timezone

try:
    from .github_monitor import GitHubMonitor
except ImportError:
    from github_monitor import GitHubMonitor

MAX_BYTES = 8192
FIELDS = {'id', 'title', 'body', 'status', 'updated_at'}


def encode(payload):
    return json.dumps(payload, ensure_ascii=True).encode('utf-8')


def validate_inbox(payload):
    if not isinstance(payload, dict) or set(payload) != {'cards'}:
        raise ValueError('invalid inbox')
    cards = payload['cards']
    if not isinstance(cards, list) or len(cards) > 100:
        raise ValueError('invalid cards')
    ids = set()
    for card in cards:
        if not isinstance(card, dict) or set(card) != FIELDS:
            raise ValueError('invalid card')
        if any(not isinstance(card[k], str) for k in FIELDS):
            raise ValueError('invalid text')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', card['id']) or card['id'] in ids:
            raise ValueError('invalid id')
        ids.add(card['id'])
        if card['status'] not in ('ok', 'warn', 'fail', 'info'):
            raise ValueError('invalid status')
        stamp = datetime.fromisoformat(card['updated_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('missing timezone')
    return cards


def bounded(cards):
    result = {'v': 1, 'cards': []}
    for source in cards[:6]:
        card = dict(source)
        card['id'] = card['id'][:64]
        card['updated_at'] = card['updated_at'][:64]
        card['title'] = card['title'][:120]
        card['body'] = card['body'][:1000]
        result['cards'].append(card)
        while len(encode(card)) > 1300 and card['body']:
            card['body'] = card['body'][:len(card['body']) // 2]
        while len(encode(card)) > 1300 and card['title']:
            card['title'] = card['title'][:len(card['title']) // 2]
        if len(encode(result)) > MAX_BYTES:
            result['cards'].pop()
    return result


class PanelFeed:
    def __init__(self, *, token=None, clock=time.monotonic):
        self.token = token
        self.clock = clock
        self.lock = threading.Lock()
        self.cached = None
        self.expires = 0

    def snapshot(self):
        with self.lock:
            if self.cached is not None and self.clock() < self.expires:
                return self.cached
            stamp = datetime.now(timezone.utc).isoformat()

            def card(key, title, body, status='info'):
                return dict(id=key, title=title, body=body, status=status, updated_at=stamp)

            try:
                repo = os.environ.get('VIBEPULSE_PANEL_REPO', 'niclasvestlund-YT/personal-agent-guide')
                github = GitHubMonitor(repo, token=self.token)
                fetch = github._fetch_json
                base = f'https://api.github.com/repos/{github.repo}'
                runs = fetch(base + '/actions/runs?branch=main&per_page=1')['workflow_runs']
                if runs:
                    run = runs[0]
                    state = run.get('conclusion') or run['status']
                    severity = 'ok' if state == 'success' else ('info' if state in ('queued', 'in_progress', 'waiting', 'pending', 'requested') else 'fail')
                    cards = [card('ci', 'Main CI', state, severity)]
                else:
                    cards = [card('ci', 'Main CI', 'No runs on main')]
                try:
                    runners = fetch(base + '/actions/runners?per_page=100')['runners']
                    runner = next((r for r in runners if r['name'] == 'lac-pc'), None)
                    if runner:
                        online = runner['status'] == 'online'
                        cards.append(card('runner', 'lac-pc', ('Online' if online else 'Offline') + (' · Busy' if runner['busy'] else ' · Idle'), 'ok' if online else 'warn'))
                    else:
                        cards.append(card('runner', 'lac-pc', 'Runner status unavailable'))
                except Exception:
                    cards.append(card('runner', 'lac-pc', 'Runner status unavailable'))
                search = fetch('https://api.github.com/search/issues?q=repo:' + github.repo + '+is:pr+is:open&per_page=1')
                count = search['total_count']
                if type(count) is not int or count < 0:
                    raise ValueError('invalid count')
                cards.append(card('prs', 'Open PRs', str(count), 'info'))
                try:
                    fetch(base + '/branches/deploy-prebuilt-lock')
                    held = True
                except urllib.error.HTTPError as exc:
                    if exc.code != 404:
                        raise
                    held = False
                cards.append(card('deploy', 'Deploy lock', 'Held' if held else 'Free', 'warn' if held else 'ok'))
                gist = os.environ.get('GIST_ID')
                if gist:
                    try:
                        if not re.fullmatch(r'[a-fA-F0-9]{1,64}', gist):
                            raise ValueError('invalid gist')
                        file = fetch('https://api.github.com/gists/' + gist)['files']['panel.json']
                        if file.get('truncated') or len(file['content'].encode('utf-8')) > 65536:
                            raise ValueError('invalid gist file')
                        inbox = validate_inbox(json.loads(file['content']))
                        # Inbox IDs cannot impersonate built-in cards.
                        if any(c['id'] in {c['id'] for c in cards} for c in inbox):
                            raise ValueError('duplicate id')
                        cards.extend(inbox)
                    except Exception:
                        cards.append(card('inbox-error', 'Inbox', 'Panel inbox unavailable'))
                # Never echo the configured credential even if supplied as inbox text.
                if self.token:
                    for item in cards:
                        for field in ('title', 'body', 'id', 'updated_at'):
                            item[field] = item[field].replace(self.token, '[redacted]')
                self.cached = bounded(cards)
            except Exception:
                self.cached = bounded([card('panel-error', 'Panel', 'GitHub status unavailable')])
            self.expires = self.clock() + 60
            return self.cached
