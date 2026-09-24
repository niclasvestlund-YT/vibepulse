"""Browser fallback contract: real readings, separate age, strict input."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from . import lovable_browser as browser
except ImportError:  # direct unittest discovery
    import lovable_browser as browser


class BrowserBalanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path_patch = patch.object(browser, 'state_dir', return_value=self.directory)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.now = 1_790_000_000
        self.clock = patch.object(browser.time, 'time', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        (self.directory / 'lovable-browser.json').write_text(json.dumps({
            'extensionId': 'a' * 32, 'workspace': 'Example workspace'}))
        self.bridge = browser.LovableBrowserBridge()
        self.reading = {'v': 1, 'workspace': 'Example workspace',
                        'credits': 35.3, 'plan': 'PRO', 'monthlyCredits': 100,
                        'dailyBuildCredits': 0, 'observedAt': self.now}
        self.official = {'v': 1, 'enabled': True, 'stale': True, 'login': True}

    def test_no_reading_preserves_official(self):
        self.assertEqual(self.bridge.snapshot(self.official), self.official)

    def test_real_numbers_without_guessed_dates_or_login_error(self):
        self.bridge.accept(self.reading)
        self.assertEqual(self.bridge.snapshot(self.official), {
            'v': 1, 'enabled': True, 'stale': False, 'creditsTenths': 353,
            'ageSeconds': 0, 'plan': 'PRO', 'grantTenths': 1000,
            'dailyCreditsTenths': 0})

    def test_age_persists_across_restart_and_becomes_stale(self):
        self.bridge.accept(self.reading)
        self.now += 181
        snap = browser.LovableBrowserBridge().snapshot(self.official)
        self.assertEqual(snap['ageSeconds'], 181)
        self.assertTrue(snap['stale'])
        self.assertEqual(snap['creditsTenths'], 353)

    def test_missing_daily_is_missing_not_zero(self):
        del self.reading['dailyBuildCredits']
        self.bridge.accept(self.reading)
        self.assertNotIn('dailyCreditsTenths', self.bridge.snapshot(self.official))

    def test_rejects_wrong_workspace_and_extra_fields(self):
        for changes in ({'workspace': 'Another workspace'}, {'access_token': 'secret'},
                        {'dailyChatPercent': 60}, {'v': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.bridge.accept({**self.reading, **changes})

    def test_rejects_invalid_numbers_and_timestamps(self):
        for key in ('credits', 'monthlyCredits', 'dailyBuildCredits', 'observedAt'):
            for value in (True, float('nan'), float('inf'), -1, '35.3', None):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.bridge.accept({**self.reading, key: value})
        for stamp in (self.now - 121, self.now + 6):
            with self.assertRaises(ValueError):
                self.bridge.accept({**self.reading, 'observedAt': stamp})

    def test_replayed_observation_does_not_refresh_age(self):
        self.bridge.accept(self.reading)
        self.now += 30
        with self.assertRaises(ValueError):
            self.bridge.accept(self.reading)
        self.assertEqual(self.bridge.snapshot(self.official)['ageSeconds'], 30)

    def test_newer_official_balance_wins(self):
        self.bridge.accept(self.reading)
        self.now += 90
        official = {**self.official, 'stale': False, 'creditsTenths': 300, 'ageSeconds': 5}
        self.assertEqual(self.bridge.snapshot(official), official)

    def test_unconfigured_bridge_rejects_readings(self):
        (self.directory / 'lovable-browser.json').unlink()
        with self.assertRaises(ValueError):
            browser.LovableBrowserBridge().accept(self.reading)

    def test_corrupt_cache_is_preserved(self):
        cache = self.directory / 'lovable-browser-cache.json'
        cache.write_text('{bad json')
        bridge = browser.LovableBrowserBridge()
        self.assertEqual(bridge.snapshot(self.official), self.official)
        self.assertEqual(len(list(self.directory.glob('*.corrupt-*'))), 1)


if __name__ == '__main__':
    unittest.main()
