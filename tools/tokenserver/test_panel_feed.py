"""Panel contract tests: all GitHub access is mocked, no credentials needed."""
import json
import os
from types import SimpleNamespace
from email.message import Message
import io
import unittest
import urllib.error
from unittest.mock import patch

from . import panel_feed as panel
from .tokenserver import Handler

STAMP = '2026-10-28T12:00:00Z'
INBOX = dict(id='note', title='Inbox', body='Ready for review', status='info', updated_at=STAMP)


def github(url):
    if '/actions/runs?' in url:
        return {'workflow_runs': [{'status': 'completed', 'conclusion': 'success'}]}
    if '/actions/runners?' in url:
        return {'runners': [{'name': 'lac-pc', 'status': 'online', 'busy': True}]}
    if '/search/issues?' in url:
        return {'total_count': 3}
    if '/branches/' in url:
        return {'name': 'deploy-prebuilt-lock'}
    return {'files': {'panel.json': {'content': json.dumps({'cards': [INBOX]})}}}


class PanelTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        fetch = patch.object(panel.GitHubMonitor, '_fetch_json', side_effect=github)
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)

    def test_schema_cache_and_repo(self):
        now = [0]
        feed = panel.PanelFeed(clock=lambda: now[0])
        payload = feed.snapshot()
        self.assertEqual(payload['v'], 1)
        panel.validate_inbox({'cards': payload['cards']})
        self.assertEqual([c['status'] for c in payload['cards']], ['ok', 'ok', 'info', 'warn'])
        calls = self.fetch.call_count
        feed.snapshot()
        self.assertEqual(self.fetch.call_count, calls)
        now[0] = 61
        with patch.dict(os.environ, {'VIBEPULSE_PANEL_REPO': 'owner/another'}):
            feed.snapshot()
        self.assertIn('/repos/owner/another/', self.fetch.call_args_list[calls].args[0])

    def test_caps_unicode_and_escaping(self):
        cards = [dict(INBOX, id=str(i), title='😀"\\' * 200, body='😀"\\' * 4000) for i in range(20)]
        payload = panel.bounded(cards)
        self.assertEqual(len(payload['cards']), 6)
        self.assertLessEqual(len(panel.encode(payload)), 8192)
        self.assertEqual(json.loads(panel.encode(payload)), payload)

    def test_inbox_and_remaining_slots(self):
        with patch.dict(os.environ, {'GIST_ID': 'abc123'}):
            payload = panel.PanelFeed().snapshot()
        self.assertEqual(payload['cards'][-1]['id'], 'note')
        self.assertEqual(len(payload['cards']), 5)
        many = [dict(INBOX, id='note' + str(i)) for i in range(10)]
        def fetch(url):
            if '/gists/' in url:
                return {'files': {'panel.json': {'content': json.dumps({'cards': many})}}}
            return github(url)
        self.fetch.side_effect = fetch
        with patch.dict(os.environ, {'GIST_ID': 'abc123'}):
            self.assertEqual(len(panel.PanelFeed().snapshot()['cards']), 6)

    def test_strict_inbox_rejection(self):
        for bad in ({}, {'cards': {}}, {'cards': [dict(INBOX, status='bad')]},
                    {'cards': [dict(INBOX, body=1)]}, {'cards': [dict(INBOX, extra='x')]},
                    {'cards': [dict(INBOX, updated_at='yesterday')]},
                    {'cards': [dict(INBOX, updated_at='2026-10-28')]},
                    {'cards': [INBOX, INBOX]}):
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                panel.validate_inbox(bad)
        self.fetch.side_effect = lambda url: ({'files': {'panel.json': {'content': 'secret malformed'}}} if '/gists/' in url else github(url))
        with patch.dict(os.environ, {'GIST_ID': 'abc123'}):
            payload = panel.PanelFeed().snapshot()
        self.assertEqual(payload['cards'][-1]['id'], 'inbox-error')
        self.assertNotIn('secret', json.dumps(payload))

    def test_failures_never_expose_secrets_and_are_cached(self):
        self.fetch.side_effect = RuntimeError('Authorization Bearer secret-token raw body')
        now = [0]
        feed = panel.PanelFeed(token='secret-token', clock=lambda: now[0])
        with patch.object(panel, 'bounded', wraps=panel.bounded):
            payload = feed.snapshot()
            feed.snapshot()
        self.assertEqual(len(payload['cards']), 4)
        self.assertEqual(payload['cards'][0]['status'], 'info')
        self.assertNotIn('secret-token', json.dumps(payload))
        self.assertEqual(self.fetch.call_count, 4)
        now[0] = 19
        feed.snapshot()
        self.assertEqual(self.fetch.call_count, 4)
        now[0] = 20
        feed.snapshot()
        self.assertEqual(self.fetch.call_count, 8)

    def test_repo_failures_preserve_bounded_inbox(self):
        many = [dict(INBOX, id='note' + str(i)) for i in range(10)]

        def fetch(url):
            if '/gists/' in url:
                return {'files': {'panel.json': {'content': json.dumps({'cards': many})}}}
            raise RuntimeError('secret repo failure')

        self.fetch.side_effect = fetch
        with patch.dict(os.environ, {'GIST_ID': 'abc123'}):
            payload = panel.PanelFeed().snapshot()
        cards = payload['cards']
        self.assertEqual([c['id'] for c in cards], ['ci', 'runner', 'prs', 'deploy', 'note0', 'note1'])
        self.assertEqual([c['body'] for c in cards[:4]],
                         ['Unavailable', 'Runner status unavailable', 'Unavailable', 'Unavailable'])
        self.assertTrue(all(c['status'] == 'info' for c in cards[:4]))
        self.assertLessEqual(len(panel.encode(payload)), 8192)
        self.assertEqual(self.fetch.call_count, 5)

    def test_gist_failure_preserves_builtin_cards(self):
        def fetch(url):
            if '/gists/' in url:
                raise RuntimeError('secret gist failure')
            return github(url)

        self.fetch.side_effect = fetch
        now = [0]
        feed = panel.PanelFeed(clock=lambda: now[0])
        with patch.dict(os.environ, {'GIST_ID': 'abc123'}):
            cards = feed.snapshot()['cards']
            self.assertEqual([c['status'] for c in cards[:4]], ['ok', 'ok', 'info', 'warn'])
            self.assertEqual(cards[-1]['id'], 'inbox-error')
            self.assertEqual(cards[-1]['body'], 'Panel inbox unavailable')
            self.assertNotIn('secret', json.dumps(cards))
            now[0] = 20
            feed.snapshot()
        self.assertEqual(self.fetch.call_count, 12)

    def test_gist_auth_failure_retries_without_credentials(self):
        for code in (401, 403, 404, 500):
            with self.subTest(code=code):
                seen = []

                def fetch(monitor, url):
                    if '/gists/' in url:
                        seen.append(monitor._token)
                        if monitor._token:
                            raise urllib.error.HTTPError(url, code, 'secret', {}, None)
                    return github(url)

                with patch.object(panel.GitHubMonitor, '_fetch_json', new=fetch), patch.dict(os.environ, {'GIST_ID': 'abc123'}):
                    cards = panel.PanelFeed(token='secret-token').snapshot()['cards']
                self.assertEqual(seen, ['secret-token', None] if code != 500 else ['secret-token'])
                self.assertEqual(cards[-1]['id'], 'note' if code != 500 else 'inbox-error')

    def test_runner_permission_and_absent_lock(self):
        def fetch(url):
            if '/actions/runners?' in url:
                raise urllib.error.HTTPError(url, 403, 'secret', {}, None)
            if '/branches/' in url:
                raise urllib.error.HTTPError(url, 404, 'secret', {}, None)
            return github(url)
        self.fetch.side_effect = fetch
        cards = panel.PanelFeed().snapshot()['cards']
        self.assertEqual(cards[1]['body'], 'Runner status unavailable')
        self.assertEqual(cards[1]['status'], 'info')
        self.assertEqual(cards[3]['body'], 'Free')

    def test_inbox_credential_is_redacted(self):
        self.fetch.side_effect = lambda url: ({'files': {'panel.json': {'content': json.dumps({'cards': [dict(INBOX, body='secret-token')]})}}} if '/gists/' in url else github(url))
        with patch.dict(os.environ, {'GIST_ID': 'abc'}):
            self.assertNotIn('secret-token', json.dumps(panel.PanelFeed(token='secret-token').snapshot()))

    def test_inbox_accepts_version_and_rejects_other_versions(self):
        card = {'id': 'x-followers', 'title': 'X followers', 'body': '31', 'status': 'info', 'updated_at': '2026-10-27T23:43:13Z'}
        self.assertEqual(panel.validate_inbox({'v': 1, 'cards': [card]}), [card])
        with self.assertRaises(ValueError):
            panel.validate_inbox({'v': 2, 'cards': [card]})

    def test_inaccessible_repo_never_reports_lock_free(self):
        import urllib.error
        def fetch(url):
            raise urllib.error.HTTPError(url, 404, 'Not Found', None, None)
        self.fetch.side_effect = fetch
        cards = panel.PanelFeed().snapshot()['cards']
        lock = next(c for c in cards if c['id'] == 'deploy')
        self.assertEqual(lock['body'], 'Unavailable')

    def test_http_endpoint(self):
        class TestHandler(Handler):
            panel_feed = panel.PanelFeed()

            def log_message(self, *args):
                pass
        handler = object.__new__(TestHandler)
        handler.path = '/api/panel'
        handler.client_address = ('127.0.0.1', 1234)
        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:8737'
        handler.server = SimpleNamespace(server_address=('127.0.0.1', 8737))
        handler.wfile = io.BytesIO()
        handler.requestline = 'GET /api/panel HTTP/1.1'
        handler.request_version = 'HTTP/1.1'
        handler.command = 'GET'
        handler.do_GET()
        raw = handler.wfile.getvalue().split(b'\r\n\r\n', 1)[1]
        self.assertLessEqual(len(raw), 8192)
        panel.validate_inbox({'cards': json.loads(raw)['cards']})
        handler.client_address = ('192.0.2.1', 1234)
        with patch.object(handler, '_send') as send:
            handler.do_GET()
        self.assertEqual(send.call_args.args[0], 403)



if __name__ == '__main__':
    unittest.main()
