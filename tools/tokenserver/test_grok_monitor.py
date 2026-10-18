"""Grok contracts with fake clocks, files and billing replies; no network."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from . import grok_monitor as grok


class GrokFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.now = 1790000000
        self.clock = lambda: self.now
        patcher = patch.object(grok, 'state_dir', return_value=self.directory)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.store = grok.GrokBotStore(self.clock)

    def post(self, state='working', bot='Research', **extra):
        self.store.accept({'v': 1, 'bot': bot, 'state': state, **extra})

    def write(self, filename, data):
        path = self.directory / filename
        path.write_text(json.dumps(data), encoding='utf-8')
        os.utime(path, (self.now, self.now))
        return path


class GrokTests(GrokFixture):
    def test_validation_and_display(self):
        self.post(bot='Re\x00search', activity='é' * 32)
        bot = self.store.snapshot()['bots'][0]
        self.assertEqual(bot['name'], 'Research')
        self.post(bot='Hårdvara')
        self.assertIn('Hardvara', [b['name'] for b in self.store.snapshot()['bots']])
        self.assertEqual(len(bot['activity'].encode()), 16)
        for change in ({'v': True}, {'bot': ''}, {'bot': '\x00'},
                       {'bot': 'x' * 33}, {'state': 'unknown'},
                       {'state': []}, {'activity': 1}, {'secret': 'no'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.accept({'v': 1, 'bot': 'ok', 'state': 'idle', **change})

    def test_leases(self):
        for state, lease in grok.LEASES.items():
            with self.subTest(state=state):
                self.post(state, state)
                self.now += lease - 1
                self.assertIn(state, [b['name'] for b in self.store.snapshot()['bots']])
                self.now += 1
                self.assertNotIn(state, [b['name'] for b in self.store.snapshot()['bots']])

    def test_priority_eviction_counts(self):
        for i in range(17):
            self.post(bot=f'bot{i}')
            self.now += 1
        snap = self.store.snapshot()
        self.assertEqual(snap['botCount'], 16)
        self.assertEqual(snap['active'], 16)
        self.assertNotIn('BOT0', self.store._bots)
        for state in ('idle', 'done', 'error', 'waiting'):
            self.post(state, state)
            self.now += 1
        self.assertEqual([b['state'] for b in self.store.snapshot()['bots']],
                         ['waiting', 'error', 'working', 'working'])

    def test_done_transitions_and_local_day(self):
        self.post('done')
        self.post('done')
        self.assertEqual(self.store.snapshot()['doneToday'], 1)
        self.post('working')
        self.post('done')
        self.assertEqual(self.store.snapshot()['doneToday'], 2)
        self.now += 86400
        self.assertEqual(self.store.snapshot()['doneToday'], 0)
        self.post('done')
        self.assertEqual(self.store.snapshot()['doneToday'], 1)

    def test_bot_file_and_invalid_logged_once(self):
        path = self.write('grok-bots.json', {'v': 1, 'bots': [
            {'bot': 'File', 'state': 'done', 'updatedAt': self.now}]})
        self.assertEqual(self.store.snapshot()['doneToday'], 1)
        self.assertEqual(self.store.snapshot()['doneToday'], 1)
        self.now += 1
        self.write(path.name, {'v': 1, 'bots': [
            {'bot': 'File', 'state': 'done', 'updatedAt': self.now}]})
        self.assertEqual(self.store.snapshot()['doneToday'], 1)
        with self.assertLogs('tokenserver.grok') as logs:
            for content in ('secret invalid', '{other invalid'):
                path.write_text(content)
                self.store.snapshot()
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn('secret', ''.join(logs.output))
        self.now += 1800
        self.assertEqual(self.store.snapshot()['bots'], [])

    def test_manual_and_auto_selection(self):
        monitor = grok.GrokMonitor(clock=self.clock)
        self.assertEqual(monitor.snapshot()['source'], 'none')
        self.write('grok.json', {'v': 1, 'usedPct': 42.9, 'resetAt': self.now + 90,
                                 'label': 'weekly'})
        data = monitor.snapshot()
        self.assertEqual((data['source'], data['usedPct'], data['resetSeconds']),
                         ('manual', 42, 90))
        self.assertEqual(data['label'], 'WEEKLY')
        self.now += 601
        self.assertTrue(monitor.snapshot()['stale'])
        self.assertEqual(monitor.snapshot()['ageSeconds'], 601)
        self.assertEqual(grok.GrokMonitor('bots', self.clock).snapshot()['source'], 'bots')
        self.assertEqual(grok.GrokMonitor('xai', self.clock).snapshot()['source'], 'none')
        for bad in (True, -1, 101, float('nan'), '42'):
            self.now += 1
            self.write('grok.json', {'v': 1, 'usedPct': bad})
            with self.assertLogs('tokenserver.grok') if not monitor.manual._file.warned else patch.object(grok.log, 'warning'):
                self.assertEqual(monitor.snapshot()['source'], 'none')
        # Auto order: xai, then live bot activity, then the manual file.
        self.now += 1
        self.write('grok.json', {'v': 1, 'usedPct': 10})
        self.assertEqual(monitor.snapshot()['source'], 'manual')
        monitor.store.accept({'v': 1, 'bot': 'Research', 'state': 'working'})
        data = monitor.snapshot()
        self.assertEqual((data['source'], data['active']), ('bots', 1))
        self.assertNotIn('usedPct', data)
        self.assertEqual(grok.GrokMonitor('manual', self.clock).snapshot()['source'],
                         'manual')
        self.now += 1
        self.write('grok.json', {'v': 1, 'usedPct': 'bad'})
        with self.assertLogs('tokenserver.grok'):
            self.assertEqual(grok.GrokMonitor('manual', self.clock).snapshot()['source'], 'none')

    def test_xai_fields_stale_secrets_and_selection(self):
        os.environ.update(XAI_MANAGEMENT_KEY='supersecret', XAI_TEAM_ID='team')
        replies = iter([
            {'coreInvoice': {'totalWithCorr': {'val': '123'}, 'amountAfterVat': '999',
                             'prepaidCredits': {'val': '-4500'}},
             'effectiveSpendingLimit': '20000'},
            RuntimeError('supersecret'),
            {'coreInvoice': {'amountAfterVat': '-5'}, 'effectiveSpendingLimit': '0'}])

        def fetch():
            result = next(replies)
            if isinstance(result, Exception):
                raise result
            return result

        monitor = grok.GrokMonitor(clock=self.clock, fetch=fetch)
        self.assertEqual(monitor.snapshot()['source'], 'none')
        self.assertNotIn('spentCents', monitor.snapshot())
        monitor.xai.poll()
        data = monitor.snapshot()
        self.assertEqual([data[k] for k in ('spentCents', 'prepaidCents', 'limitCents')],
                         [123, 4500, 20000])
        self.now += 30
        with self.assertLogs('tokenserver.grok') as logs:
            monitor.xai.poll()
        data = monitor.snapshot()
        self.assertTrue(data['stale'])
        self.assertEqual(data['ageSeconds'], 30)
        self.assertEqual(data['spentCents'], 123)
        self.assertNotIn('supersecret', json.dumps(data) + ''.join(logs.output))
        monitor.xai.poll()
        self.assertFalse(monitor.snapshot()['stale'])
        self.assertEqual(monitor.snapshot()['spentCents'], 0)
        self.assertNotIn('limitCents', monitor.snapshot())
        self.now += 601
        self.assertTrue(monitor.snapshot()['stale'])

    def test_thread_first_poll_and_stop(self):
        import threading
        os.environ.update(XAI_MANAGEMENT_KEY='secret', XAI_TEAM_ID='team')
        called = threading.Event()

        def fetch():
            called.set()
            return {'coreInvoice': {'amountAfterVat': '7'}}

        source = grok.XaiApiUsageSource(fetch, self.clock)
        source.start()
        self.addCleanup(source.stop)
        self.assertTrue(called.wait(1))
        source.stop()
        self.assertIsNone(source._thread)

    def test_payload_bound_and_disabled(self):
        os.environ.update(XAI_MANAGEMENT_KEY='secret', XAI_TEAM_ID='team')
        monitor = grok.GrokMonitor(clock=self.clock, fetch=lambda: {
            'coreInvoice': {'amountAfterVat': str(10**50),
                            'prepaidCredits': {'val': str(-10**50)}},
            'effectiveSpendingLimit': str(10**50)})
        monitor.xai.poll()
        for i in range(16):
            monitor.store.accept({'v': 1, 'bot': str(i) + 'é' * 30,
                                  'state': 'waiting', 'activity': '\\"' * 16})
        data = grok.grok_snapshot(monitor)
        self.assertLess(len(json.dumps(data).encode()), 480)
        self.assertLessEqual(len(data['bots']), 4)
        self.assertEqual(data['active'], 16)
        self.assertEqual(grok.grok_snapshot(), {'v': 1, 'enabled': False})

    def test_manual_schema_and_recovery(self):
        source = grok.ManualQuotaSource(self.clock)
        self.assertIsNone(source.snapshot())
        for extra in ({'label': 'x' * 17}, {'resetAt': True}, {'resetAt': -1},
                      {'label': 123}, {'extra': 'secret'}, {'v': True}):
            self.now += 1
            self.write('grok.json', {'v': 1, 'usedPct': 5, **extra})
            with patch.object(grok.log, 'warning'):
                self.assertIsNone(source.snapshot())
        self.now += 1
        path = self.write('grok.json', {'v': 1, 'usedPct': 0})
        data = source.snapshot()
        self.assertEqual(data['usedPct'], 0)
        self.assertNotIn('resetSeconds', data)
        self.assertNotIn('label', data)
        path.unlink()
        self.assertIsNone(source.snapshot())

    def test_xai_unconfigured_never_fetches(self):
        fetch = Mock()
        os.environ['XAI_MANAGEMENT_KEY'] = 'secret'
        source = grok.XaiApiUsageSource(fetch, self.clock)
        source.start()
        source.poll()
        source.stop()
        self.assertIsNone(source.snapshot())
        fetch.assert_not_called()

    def test_cli_posts_expected_headers_and_reports_failure(self):
        from . import grok_status
        args = ['grok_status.py', '--bot', 'Research', '--state', 'working',
                '--activity', 'searching']
        with patch('sys.argv', args), patch.object(grok_status, 'urlopen') as opener:
            self.assertEqual(grok_status.main(), 0)
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:8737/api/grok/bots')
        self.assertEqual(request.get_header('Content-type'), 'application/json')
        self.assertFalse(request.has_header('Origin'))
        self.assertEqual(json.loads(request.data), {
            'v': 1, 'bot': 'Research', 'state': 'working', 'activity': 'searching'})
        with patch('sys.argv', args), patch.object(grok_status, 'urlopen',
                                                  side_effect=OSError()), \
                patch('sys.stderr') as stderr:
            self.assertEqual(grok_status.main(), 1)
            self.assertTrue(stderr.write.called)

    def test_done_is_not_recounted_after_expiry_eviction_or_replay(self):
        self.post('done', 'solo')
        self.now += 1801
        self.post('done', 'solo')
        self.assertEqual(self.store.snapshot()['doneToday'], 1)
        self.post('working', 'solo')
        self.post('done', 'solo')
        self.assertEqual(self.store.snapshot()['doneToday'], 2)
        bots = [{'bot': f'bot{i}', 'state': 'done', 'updatedAt': self.now}
                for i in range(17)]
        self.write('grok-bots.json', {'v': 1, 'bots': bots})
        first = self.store.snapshot()['doneToday']
        self.now += 1
        self.write('grok-bots.json', {'v': 1, 'bots': bots})
        self.assertEqual(self.store.snapshot()['doneToday'], first)
        self.assertEqual(first, 2 + 17)

    def test_transient_read_error_is_retried_without_file_change(self):
        self.write('grok-bots.json', {'v': 1, 'bots': [
            {'bot': 'filebot', 'state': 'working', 'updatedAt': self.now}]})
        real = Path.read_text
        with patch.object(Path, 'read_text', side_effect=PermissionError('locked')), \
                self.assertLogs('tokenserver.grok'):
            self.assertEqual(self.store.snapshot()['bots'], [])
        with patch.object(Path, 'read_text', real):
            self.assertEqual(self.store.snapshot()['bots'][0]['name'], 'filebot')

    def test_billing_request_refuses_redirects(self):
        from urllib.request import Request
        handler = grok._NoRedirect()
        with self.assertRaises(ValueError):
            handler.redirect_request(Request('https://management-api.x.ai/x'),
                                     None, 302, 'Found', {}, 'http://evil.example/')


class OpenRouterTests(GrokFixture):
    def setUp(self):
        super().setUp()
        os.environ['OPENROUTER_API_KEY'] = 'fake-api-secret'
        self.opener = Mock()
        self.client = grok.Client(self.opener)
        self.source = grok.OpenRouterUsageSource(self.client, self.clock)

    def reply(self, *data):
        responses = []
        for item in data:
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = json.dumps({'data': item}).encode()
            responses.append(response)
        self.opener.open.side_effect = responses

    def test_key_credits_budget_and_age(self):
        self.source._management_key = 'fake-management-secret'
        self.reply({'usage_monthly': 12.34, 'usage': 999, 'limit': 100,
                    'effective_budget': {'limit_usd': 25, 'spend_usd': 12.34,
                                         'resets_at': '2026-09-21T14:15:00Z'}},
                   {'total_credits': 80, 'total_usage': 12.5})
        self.source.poll()
        data = self.source.snapshot()
        self.assertEqual([data[k] for k in ('source', 'label', 'spentCents',
                                           'limitCents', 'prepaidCents')],
                         ['openrouter', 'OPENROUTER', 1234, 2500, 6750])
        reset = data['resetSeconds']
        self.now += 30
        self.assertEqual(self.source.snapshot()['resetSeconds'], max(0, reset - 30))
        self.assertEqual(self.source.snapshot()['ageSeconds'], 30)
        calls = self.opener.open.call_args_list
        self.assertEqual([c.args[0].full_url for c in calls],
                         ['https://openrouter.ai/api/v1/key',
                          'https://openrouter.ai/api/v1/credits'])
        self.assertEqual(calls[0].args[0].get_header('Authorization'),
                         'Bearer fake-api-secret')
        self.assertEqual(calls[1].args[0].get_header('Authorization'),
                         'Bearer fake-management-secret')
        self.assertEqual(calls[0].kwargs['timeout'], 8)
        self.now += 601
        self.assertTrue(self.source.snapshot()['stale'])

    def test_limits_missing_values_and_month_math(self):
        from datetime import datetime, timezone
        for date, following in [('2024-02-29', '2024-03-01'),
                                ('2026-12-31', '2027-01-01')]:
            self.now = datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()
            expected = datetime.fromisoformat(following).replace(tzinfo=timezone.utc).timestamp()
            for extra in ({'limit': 20, 'limit_reset': 'monthly'},
                          {'limit': None},
                          {'usage_monthly': None, 'limit': 0, 'limit_reset': 'monthly'}):
                with self.subTest(date=date, extra=extra):
                    self.reply({'usage_monthly': 1.23, **extra})
                    self.source.poll()
                    data = self.source.snapshot()
                    self.assertEqual(data['resetSeconds'], expected - self.now)
                    self.assertNotIn('prepaidCents', data)
                    if extra.get('limit') is None:
                        self.assertNotIn('limitCents', data)
                    else:
                        self.assertEqual(data['limitCents'], extra['limit'] * 100)
                    if extra.get('usage_monthly', 1) is None:
                        self.assertNotIn('spentCents', data)
        # A lifetime budget: its own spend and limit, no reset.
        self.reply({'usage_monthly': 2, 'limit': 50,
                    'effective_budget': {'limit_usd': 50, 'spend_usd': 7, 'resets_at': None}})
        self.source.poll()
        self.assertEqual(self.source.snapshot()['limitCents'], 5000)
        self.assertEqual(self.source.snapshot()['spentCents'], 700)
        self.assertNotIn('resetSeconds', self.source.snapshot())
        self.assertNotIn('windowSeconds', self.source.snapshot())
        self.reply({'effective_budget': {'limit_usd': 50, 'spend_usd': 7, 'reset_interval': 'weekly',
                                         'resets_at': '2027-01-04T00:00:00Z'}})
        self.source.poll()
        self.assertEqual(self.source.snapshot()['windowSeconds'], 604800)

    def test_byok_counts_against_key_limit_only_when_flagged(self):
        for flag, spent in ((True, 900), (False, 100)):
            with self.subTest(flag=flag):
                self.reply({'usage_daily': 1, 'byok_usage_daily': 8, 'limit': 10,
                            'limit_reset': 'daily', 'include_byok_in_limit': flag})
                self.source.poll()
                self.assertEqual(self.source.snapshot()['spentCents'], spent)

    def test_key_limit_uses_spend_from_the_same_window(self):
        from datetime import datetime, timezone
        self.now = datetime(2026, 10, 14, 6, tzinfo=timezone.utc).timestamp()  # a Wednesday
        usage = {'usage': 90, 'usage_daily': 1, 'usage_weekly': 4, 'usage_monthly': 9}
        for window, spent, reset in (('daily', 100, datetime(2026, 10, 15, tzinfo=timezone.utc)),
                                     ('weekly', 400, datetime(2026, 10, 19, tzinfo=timezone.utc)),
                                     ('monthly', 900, datetime(2026, 11, 1, tzinfo=timezone.utc)),
                                     (None, 9000, None)):
            with self.subTest(window=window):
                self.reply({**usage, 'limit': 100, 'limit_reset': window})
                self.source.poll()
                data = self.source.snapshot()
                self.assertEqual((data['spentCents'], data['limitCents']), (spent, 10000))
                self.assertEqual(data.get('windowSeconds'),
                                 {'daily': 86400, 'weekly': 604800, 'monthly': 31 * 86400}.get(window))
                if reset is None:
                    self.assertNotIn('resetSeconds', data)
                else:
                    self.assertEqual(data['resetSeconds'], reset.timestamp() - self.now)

    def test_auth_error_retains_cache_and_no_secrets(self):
        from urllib.error import HTTPError
        from io import BytesIO
        from .openrouter_client import SourceError
        error = lambda: HTTPError('https://openrouter.ai/api/v1/key', 401,
                                  'fake-api-secret', {}, BytesIO(b'fake-api-secret'))
        self.opener.open.side_effect = error()
        with self.assertRaisesRegex(SourceError, '^auth$'):
            self.client.get('key', 'fake-api-secret')
        with self.assertLogs('tokenserver.grok') as logs:
            self.opener.open.side_effect = error()
            self.source.poll()
        self.assertIsNone(self.source.snapshot())
        self.reply({'usage_monthly': 3})
        self.source.poll()
        self.now += 30
        self.opener.open.side_effect = error()
        with self.assertLogs('tokenserver.grok') as cached_logs:
            self.source.poll()
        data = self.source.snapshot()
        self.assertEqual(data['spentCents'], 300)
        self.assertTrue(data['stale'])
        self.assertEqual(data['ageSeconds'], 30)
        self.assertNotIn('fake-api-secret', json.dumps(data) +
                         ''.join(logs.output + cached_logs.output))

    def test_redirect_body_bound_and_invalid_numbers(self):
        from urllib.request import Request
        from .openrouter_client import NoRedirect, SourceError
        self.assertIsNone(NoRedirect().redirect_request(
            Request('https://openrouter.ai/api/v1/key'), None, 302, 'Found', {},
            'https://evil.example/'))
        self.reply({})
        # Reconfigure an oversized response without any network request.
        self.opener.open.side_effect = None
        from unittest.mock import MagicMock
        self.opener.open.return_value = MagicMock()
        self.opener.open.return_value.__enter__.return_value.read.return_value = b'x' * 65537
        with self.assertRaisesRegex(SourceError, 'invalid_response'):
            self.client.get('key', 'fake-api-secret')
        self.opener.open.return_value.__enter__.return_value.read.assert_called_with(65537)
        for value in (True, '12', float('nan'), float('inf')):
            self.reply({'usage_monthly': value})
            with self.assertLogs('tokenserver.grok'):
                self.source.poll()
            self.assertIsNone(self.source.snapshot())

    def test_auto_order_and_payload_bound(self):
        os.environ.update(XAI_MANAGEMENT_KEY='fake-xai', XAI_TEAM_ID='team')
        monitor = grok.GrokMonitor(clock=self.clock, fetch=lambda: {
            'coreInvoice': {'amountAfterVat': '10'}})
        monitor.openrouter = self.source
        self.write('grok.json', {'v': 1, 'usedPct': 10})
        self.assertEqual(monitor.snapshot()['source'], 'manual')
        monitor.store.accept({'v': 1, 'bot': 'bot', 'state': 'working'})
        self.assertEqual(monitor.snapshot()['source'], 'bots')
        self.reply({'usage_monthly': 10**20, 'limit': 10**20})
        self.source.poll()
        self.assertEqual(monitor.snapshot()['source'], 'openrouter')
        for i in range(16):
            monitor.store.accept({'v': 1, 'bot': str(i) + 'é' * 30,
                                  'state': 'waiting', 'activity': '\\"' * 16})
        data = monitor.snapshot()
        self.assertEqual(data['v'], 1)
        self.assertLess(len(json.dumps(data, allow_nan=False).encode()), 480)
        monitor.xai.poll()
        self.assertEqual(monitor.snapshot()['source'], 'xai')

    def test_unconfigured_and_optional_credit_failure(self):
        del os.environ['OPENROUTER_API_KEY']
        source = grok.OpenRouterUsageSource(self.client, self.clock)
        source.start()
        source.poll()
        source.stop()
        self.opener.open.assert_not_called()
        self.assertIsNone(source.snapshot())
        self.source._management_key = 'fake-management-secret'
        self.reply({'usage_monthly': 5})
        replies = list(self.opener.open.side_effect)
        self.opener.open.side_effect = replies + [RuntimeError('fake-management-secret')]
        with self.assertLogs('tokenserver.grok') as logs:
            self.source.poll()
        self.assertEqual(self.source.snapshot()['spentCents'], 500)
        self.assertNotIn('prepaidCents', self.source.snapshot())
        self.assertNotIn('fake-management-secret', ''.join(logs.output))

    def test_poll_thread_and_cli_environment_selection(self):
        from . import tokenserver
        import threading
        called = threading.Event()
        self.reply({'usage_monthly': 1})
        original = self.source.poll

        def poll():
            original()
            called.set()

        with patch.object(self.source, 'poll', side_effect=poll):
            self.source.start()
            self.addCleanup(self.source.stop)
            self.assertTrue(called.wait(1))
            self.source.stop()
        self.assertIsNone(self.source._thread)
        self.source._stop = Mock()
        self.source._stop.is_set.return_value = False
        self.source._stop.wait.return_value = True
        with patch.object(self.source, 'poll') as fetch:
            self.source._run()
        fetch.assert_called_once_with()
        self.source._stop.wait.assert_called_once_with(300)
        os.environ['VIBEPULSE_GROK_SOURCE'] = 'openrouter'
        self.assertEqual(tokenserver._build_arg_parser().parse_args([]).grok_source,
                         'openrouter')
        self.assertEqual(tokenserver._build_arg_parser().parse_args(
            ['--grok-source', 'openrouter']).grok_source, 'openrouter')
        monitor = grok.GrokMonitor('openrouter', clock=self.clock)
        with patch.object(monitor.openrouter, 'start') as start, \
                patch.object(monitor.openrouter, 'stop') as stop, \
                patch.object(monitor.xai, 'start') as xai:
            monitor.start()
            monitor.stop()
        start.assert_called_once_with()
        stop.assert_called_once_with()
        xai.assert_not_called()
