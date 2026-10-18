"""Default-off Grok activity and explicitly sourced usage; no relay data."""
import json
import logging
import math
import os
import threading
import time
import unicodedata
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

if __package__:
    from .state_files import state_dir
    from .openrouter_client import Client
else:
    from state_files import state_dir
    from openrouter_client import Client

log = logging.getLogger('tokenserver.grok')
MAX_BOT_HISTORY = 4096
MAX_INT = 2147483647
MAX_EPOCH = 2**32  # Through 2106; safe for arithmetic and datetime conversion.
STATES = ('waiting', 'error', 'working', 'done', 'idle')
LEASES = {'working': 600, 'waiting': 7200, 'error': 1800, 'done': 1800, 'idle': 1800}


def bounded(value):
    return max(0, min(MAX_INT, int(value)))


def text(value):
    if not isinstance(value, str):
        raise ValueError('invalid text')
    clean = ''.join(c for c in value if not unicodedata.category(c).startswith('C'))
    return clean


def display(value, upper=False):
    # The panel's UI fonts carry printable ASCII only; fold accents and drop
    # anything else rather than shipping glyphs that render as boxes. Names
    # and activities keep their case ("Hardvara"); captions are uppercase.
    value = text(value)
    folded = unicodedata.normalize('NFKD', value.upper() if upper else value)
    ascii_text = ''.join(c for c in folded if ' ' <= c <= '~').strip()
    return ascii_text[:16].strip()


def number(value):
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
        raise ValueError('invalid number')
    return value


class JsonFile:
    """Validate only changed content; malformed input never leaks into logs."""
    def __init__(self, path):
        self.path = path
        self.signature = None
        self.data = None
        self.mtime = None
        self.warned = False
        self.invalid = False
        self.generation = 0

    def read(self, validate):
        try:
            stat = self.path.stat()
            if stat.st_size > 65536:
                self.signature = None
                raise ValueError('oversized file')
            raw = self.path.read_bytes()
            if len(raw) > 65536:
                self.signature = None
                raise ValueError('oversized file')
            signature = raw
            if signature == self.signature:
                return self.data
            # Only content is cached by signature. A transient I/O error
            # (locked file, permissions) must be retried on the next poll.
            self.signature = signature
            data = validate(json.loads(raw.decode('utf-8')))
            self.data, self.mtime = data, stat.st_mtime
            self.invalid = False
            self.generation += 1
        except FileNotFoundError:
            self.signature = self.data = self.mtime = None
            self.invalid = False
        except OSError:
            self.signature = None
            self.invalid = True
            if not self.warned:
                log.warning('%s: could not be read; retrying', self.path.name)
                self.warned = True
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError,
                UnicodeDecodeError):
            self.invalid = True
            if not self.warned:
                log.warning('%s: invalid input ignored', self.path.name)
                self.warned = True
        return self.data


class GrokBotStore:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._lock = threading.RLock()
        self._bots = {}
        self._day = None
        self._done = 0
        # Last accepted record per bot name, apart from display leases
        # and eviction so an expired or replayed "done" never counts twice.
        self._last_state = {}
        self._file = JsonFile(state_dir() / 'grok-bots.json')
        self._loaded = None

    def _validate(self, data, file=False):
        required = {'bot', 'state'} | ({'updatedAt'} if file else {'v'})
        if (not isinstance(data, dict) or not required <= data.keys() or
                data.keys() - required - {'activity'} or
                (not file and (type(data['v']) is not int or data['v'] != 1))):
            raise ValueError('invalid bot schema')
        name = text(data['bot'])
        if (not 1 <= len(name) <= 32 or not display(name) or
                data['state'] not in STATES):
            raise ValueError('invalid bot')
        activity = display(data['activity']) if 'activity' in data else ''
        stamp = number(data['updatedAt']) if file else self.clock()
        if not 0 <= stamp <= MAX_EPOCH or stamp > self.clock():
            raise ValueError('invalid timestamp')
        return (name.upper(), {'name': display(name), 'state': data['state'],
                               'activity': activity, 'updatedAt': stamp})

    def _roll_day(self):
        day = datetime.fromtimestamp(self.clock()).date()
        if day != self._day:
            self._day, self._done = day, 0
            self._last_state = {name: bot for name, bot in self._last_state.items()
                                if bot['state'] == 'done'}

    def _put(self, name, bot, file=False):
        self._roll_day()
        old = self._last_state.get(name)
        if old and old['updatedAt'] >= bot['updatedAt']:
            # Live updates may share a clock tick; exact file replays cannot.
            if file or old == bot or old['updatedAt'] > bot['updatedAt']:
                return
        # Never evict daily history: replaying an evicted name would recount it.
        # At capacity, unknown names remain visible but cannot affect the count.
        if old is not None or len(self._last_state) < MAX_BOT_HISTORY:
            if bot['state'] == 'done' and (old is None or old['state'] != 'done'):
                if datetime.fromtimestamp(bot['updatedAt']).date() == self._day:
                    self._done = min(MAX_INT, self._done + 1)
            self._last_state[name] = bot
        self._bots[name] = bot
        if len(self._bots) > 16:
            del self._bots[min(self._bots, key=lambda key: self._bots[key]['updatedAt'])]

    def accept(self, data):
        name, bot = self._validate(data)
        with self._lock:
            self._refresh()
            self._put(name, bot)

    def _refresh(self):
        def validate(data):
            if (not isinstance(data, dict) or set(data) != {'v', 'bots'} or
                    type(data['v']) is not int or data['v'] != 1 or
                    not isinstance(data['bots'], list) or len(data['bots']) > 256):
                raise ValueError('invalid bots file')
            records = [self._validate(bot, file=True) for bot in data['bots']]
            if len({name for name, bot in records}) != len(records):
                raise ValueError('duplicate bot names')
            return records
        records = self._file.read(validate)
        if records is not None and self._file.generation != self._loaded:
            self._loaded = self._file.generation
            for name, bot in records:
                self._put(name, bot, file=True)
        now = self.clock()
        self._bots = {k: b for k, b in self._bots.items()
                      if now - b['updatedAt'] < LEASES[b['state']]}
        self._roll_day()

    def snapshot(self):
        with self._lock:
            self._refresh()
            bots = sorted(self._bots.values(),
                          key=lambda b: (STATES.index(b['state']), -b['updatedAt']))
            return {'active': sum(b['state'] in ('working', 'waiting') for b in bots),
                    'doneToday': self._done, 'botCount': len(bots),
                    'bots': [{k: b[k] for k in ('name', 'state', 'activity')
                              if k != 'activity' or b[k]} for b in bots[:4]]}


class BotActivitySource:
    def __init__(self, store):
        self.store = store

    def snapshot(self, allow_empty=False):
        data = self.store.snapshot()
        if not allow_empty and not data['botCount'] and not data['doneToday']:
            return None  # no bot has reported: fall through to the next source
        return {'source': 'bots', 'stale': False,
                'active': data['active'], 'doneToday': data['doneToday']}


class PlanQuotaSource:
    def __init__(self, clock=time.time, store=None):
        self.clock = clock
        self.store = store
        self._file = JsonFile(state_dir() / 'grok.json')
        self._lock = threading.Lock()

    @staticmethod
    def _validate(data):
        if (not isinstance(data, dict) or type(data.get('v')) is not int or
                data['v'] != 1):
            raise ValueError('invalid plan')
        common = {'v', 'plan', 'window', 'resetAt'}
        if 'usedPct' in data:
            allowed, required = common | {'usedPct', 'label'}, {'usedPct'}
            if type(data['usedPct']) is not int or not 0 <= data['usedPct'] <= 100:
                raise ValueError('invalid percentage')
        elif 'estimateFrom' in data:
            allowed = {'v', 'plan', 'window', 'limit', 'estimateFrom'}
            required = {'window', 'limit', 'estimateFrom'}
            if data['estimateFrom'] != 'bots' or data.get('window') != 'daily':
                raise ValueError('invalid estimate')
        else:
            allowed, required = common | {'used', 'limit'}, {'window', 'used', 'limit'}
            if type(data.get('used')) is not int or not 0 <= data['used'] <= 10000000:
                raise ValueError('invalid usage')
        if data.keys() - allowed or not required <= data.keys():
            raise ValueError('invalid plan schema')
        if 'limit' in data and (type(data['limit']) is not int or
                               not 1 <= data['limit'] <= 1000000):
            raise ValueError('invalid limit')
        if 'window' in data and data['window'] not in ('daily', 'weekly'):
            raise ValueError('invalid window')
        if 'resetAt' in data and not 0 <= number(data['resetAt']) <= MAX_EPOCH:
            raise ValueError('invalid reset')
        if 'label' in data and len(text(data['label'])) > 16:
            raise ValueError('invalid label')
        if 'plan' in data:
            text(data['plan'])
        return data

    def snapshot(self):
        with self._lock:
            data = self._file.read(self._validate)
            if data is None:
                return None
            live = data.get('estimateFrom') == 'bots'
            result = {'source': 'plan', 'stale': self._file.invalid,
                      'estimated': 'usedPct' not in data}
            if not live:
                age = bounded(self.clock() - self._file.mtime)
                result.update(ageSeconds=age, stale=self._file.invalid or age > 600)
            if 'usedPct' in data:
                result['usedPct'] = data['usedPct']
            else:
                used = self.store.snapshot()['doneToday'] if live else data['used']
                result.update(usedPct=min(100, used * 100 // data['limit']),
                              planUsed=used, planLimit=data['limit'])
            window = data.get('window')
            if window:
                result['windowSeconds'] = 86400 if window == 'daily' else 604800
            if 'resetAt' in data:
                result['resetSeconds'] = bounded(data['resetAt'] - self.clock())
                result['stale'] |= data['resetAt'] <= self.clock()
            elif window == 'daily':
                tomorrow = datetime.fromtimestamp(self.clock()).date() + timedelta(days=1)
                midnight = datetime.combine(tomorrow, datetime.min.time()).timestamp()
                result['resetSeconds'] = bounded(midnight - self.clock())
            if 'label' in data or window:
                result['label'] = display(data.get('label', window), upper=True)
            if 'plan' in data:
                result['planName'] = display(data['plan'], upper=True)[:12].strip()
            return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('xAI billing redirect refused')


class XaiApiUsageSource:
    def __init__(self, fetch=None, clock=time.time):
        self.clock = clock
        self._key = os.environ.get('XAI_MANAGEMENT_KEY')
        self._team = os.environ.get('XAI_TEAM_ID')
        self.configured = bool(self._key and self._team)
        self._fetch = fetch or self._request
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._data = None
        self._success = None
        self._failed = False
        self._failures = 0
        self._retry_after = 0

    def _record_failure(self, error):
        self._failed = True
        self._failures += 1
        self._retry_after = 0
        status = error.code if isinstance(error, HTTPError) else getattr(error, 'status', None)
        retry = (error.headers.get('Retry-After') if isinstance(error, HTTPError)
                 and error.headers else getattr(error, 'retry_after', None))
        if status in (429, 503):
            try:
                seconds = float(retry)
                if math.isfinite(seconds) and seconds >= 0:
                    self._retry_after = seconds
            except (ValueError, TypeError):
                pass

    def _poll_delay(self):
        with self._lock:
            backoff = min(3600, 300 * 2 ** min(max(0, self._failures - 1), 4))
            return min(21600, max(backoff, self._retry_after))

    def _record_success(self):
        self._failed = False
        self._failures = 0
        self._retry_after = 0

    def _request(self):
        # Never follow redirects: urllib would forward the explicitly set
        # Authorization header to whatever host the redirect names.
        opener = build_opener(_NoRedirect)
        request = Request('https://management-api.x.ai/v1/billing/teams/' +
                          quote(self._team, safe='') + '/postpaid/invoice/preview',
                          headers={'Authorization': 'Bearer ' + self._key})
        with opener.open(request, timeout=15) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError('oversized response')
            return json.loads(raw)

    def poll(self):
        if not self.configured:
            return
        try:
            reply = self._fetch()
            core = reply['coreInvoice']
            result = {}
            value = core.get('totalWithCorr', {}).get('val')
            if value is None:
                value = core.get('amountAfterVat')
            if value is not None:
                value = number(int(value) if isinstance(value, str) else value)
                if value < 0:
                    raise ValueError('negative spend')
                result['spentCents'] = bounded(int(value))
            value = core.get('prepaidCredits', {}).get('val')
            if value is not None:
                value = number(int(value) if isinstance(value, str) else value)
                result['prepaidCents'] = bounded(-int(value))
            value = reply.get('effectiveSpendingLimit')
            if value is not None:
                value = number(int(value) if isinstance(value, str) else value)
                if value > 0:
                    result['limitCents'] = bounded(int(value))
            if not result:
                raise ValueError('missing billing values')
        except Exception as error:
            # Exception messages/HTTP bodies can contain credentials. Never log them.
            log.warning('xAI billing poll failed; retaining last good reading')
            with self._lock:
                self._record_failure(error)
            return
        with self._lock:
            self._data, self._success = result, self.clock()
            self._record_success()

    def _run(self):
        while not self._stop.is_set():
            self.poll()
            if self._stop.wait(self._poll_delay()):
                break

    def start(self):
        if self.configured and self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=16)
            self._thread = None

    def snapshot(self):
        if not self.configured:
            return None
        with self._lock:
            if self._data is None:
                return None
            result = {'source': 'xai', 'label': 'XAI POSTPAID', 'stale': True}
            if self._data is not None:
                age = bounded(self.clock() - self._success)
                result.update(self._data, ageSeconds=age,
                              stale=self._failed or age > 600)
            return result


class OpenRouterUsageSource(XaiApiUsageSource):
    def __init__(self, client=None, clock=time.time):
        super().__init__(clock=clock)
        self._key = os.environ.get('OPENROUTER_API_KEY')
        self._management_key = os.environ.get('OPENROUTER_MANAGEMENT_KEY')
        self.configured = bool(self._key)
        self._client = client or Client()

    @staticmethod
    def _cents(value):
        value = number(value)
        if value < 0:
            raise ValueError('negative billing value')
        return bounded(round(value * 100))

    def _next_utc(self, window):
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        if window == 'daily':
            return day.timestamp() + 86400
        if window == 'weekly':  # OpenRouter weeks start Monday 00:00 UTC
            return day.timestamp() + 86400 * (7 - now.weekday())
        return datetime(now.year + (now.month == 12), now.month % 12 + 1, 1,
                        tzinfo=timezone.utc).timestamp()

    def _window(self, window):
        # Length of the spend window, so the panel can label it and place
        # its pace marker; the month is the current UTC calendar month.
        if window == 'daily':
            return 86400
        if window == 'weekly':
            return 7 * 86400
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        start = datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp()
        return int(self._next_utc('monthly') - start)

    def poll(self):
        if not self.configured:
            return
        try:
            key = self._client.get('key', self._key)
            if ('include_byok_in_limit' in key
                    and not isinstance(key['include_byok_in_limit'], bool)):
                raise ValueError('invalid BYOK flag')
            result = {}
            budget = key.get('effective_budget')
            if budget is not None and not isinstance(budget, dict):
                raise ValueError('invalid budget')
            if budget:
                # The budget that blocks first carries its own window, spend
                # and reset instant; show exactly that.
                for field, name in (('spend_usd', 'spentCents'), ('limit_usd', 'limitCents')):
                    if budget.get(field) is not None:
                        result[name] = self._cents(budget[field])
                reset = budget.get('resets_at')
                if reset is not None:
                    stamp = datetime.fromisoformat(reset.replace('Z', '+00:00'))
                    if stamp.tzinfo is None:
                        raise ValueError('invalid reset')
                    result['resetAt'] = stamp.timestamp()
                if budget.get('reset_interval') in ('daily', 'weekly', 'monthly'):
                    result['windowSeconds'] = self._window(budget['reset_interval'])
            else:
                # No budget: compare the key limit with spend from the same
                # UTC window (usage_daily/weekly/monthly); unlimited keys show
                # month-to-date spend.
                limit, window = key.get('limit'), key.get('limit_reset')
                if limit is None:
                    field, window = 'usage_monthly', 'monthly'
                elif window in ('daily', 'weekly', 'monthly'):
                    field = 'usage_' + window
                elif window is None:
                    field = 'usage'  # a limit that never resets counts all-time use
                else:
                    raise ValueError('unknown limit window')
                if key.get(field) is not None:
                    spent = number(key[field])
                    # BYOK spend counts against the key limit when the key says so.
                    if limit is not None and key.get('include_byok_in_limit') is True:
                        spent += number(key.get('byok_' + field))
                    result['spentCents'] = self._cents(spent)
                if limit is not None:
                    result['limitCents'] = self._cents(limit)
                if window is not None:
                    result['resetAt'] = self._next_utc(window)
                    result['windowSeconds'] = self._window(window)
            if not any(name in result for name in ('spentCents', 'limitCents')):
                raise ValueError('missing billing values')
        except Exception as error:
            log.warning('OpenRouter billing poll failed; retaining last good reading')
            with self._lock:
                self._record_failure(error)
            return
        # Account credits are optional: their failure must not discard key spend.
        failed = False
        if self._management_key:
            try:
                credits = self._client.get('credits', self._management_key)
                total, used = credits.get('total_credits'), credits.get('total_usage')
                total, used = number(total), number(used)
                if total < 0 or used < 0:
                    raise ValueError('negative credits operand')
                remaining = total - used
                if remaining < 0:
                    raise ValueError('negative balance')
                result['prepaidCents'] = self._cents(remaining)
            except Exception as error:
                failure = error
                log.warning('OpenRouter credits poll failed; omitting account credits')
                failed = True
        with self._lock:
            self._data, self._success = result, self.clock()
            if failed:
                self._record_failure(failure)
            else:
                self._record_success()

    def snapshot(self):
        if not self.configured:
            return None
        with self._lock:
            if self._data is None:
                return None
            age = bounded(self.clock() - self._success)
            result = {'source': 'openrouter', 'label': 'OPENROUTER',
                      'stale': self._failed or age > 600, 'ageSeconds': age,
                      **self._data}
            if 'resetAt' in result:
                reset = result.pop('resetAt')
                result['resetSeconds'] = bounded(reset - self.clock())
                result['stale'] |= reset <= self.clock()
            return result


def grok_snapshot(monitor=None):
    if monitor is None:
        return {'v': 1, 'enabled': False}
    return monitor.snapshot()


class GrokMonitor:
    def __init__(self, source='auto', clock=time.time, fetch=None):
        if source not in ('auto', 'subscription', 'xai', 'openrouter', 'manual', 'bots'):
            raise ValueError('invalid Grok source')
        self.source = 'plan' if source in ('subscription', 'manual') else source
        self.store = GrokBotStore(clock)
        self.xai = XaiApiUsageSource(fetch, clock)
        self.openrouter = OpenRouterUsageSource(clock=clock)
        self.plan = PlanQuotaSource(clock, self.store)
        self.bots = BotActivitySource(self.store)
        self._lock = threading.Lock()

    def start(self):
        if self.source in ('auto', 'xai'):
            self.xai.start()

        if self.source in ('auto', 'openrouter'):
            self.openrouter.start()

    def stop(self):
        self.xai.stop()
        self.openrouter.stop()

    def snapshot(self):
        with self._lock:
            data = None
            # A valid subscription plan takes priority over optional API sources.
            for name in (('plan', 'xai', 'openrouter', 'bots') if self.source == 'auto'
                         else (self.source,)):
                source = getattr(self, name)
                # An explicitly chosen bot source reports real zeros; in auto
                # mode an empty bot store supplies no reading.
                data = (source.snapshot(allow_empty=self.source == 'bots')
                        if name == 'bots' else source.snapshot())
                if data is not None:
                    break
            result = {'v': 1, 'enabled': True, **(data or {'source': 'none', 'stale': True})}
            bots = self.store.snapshot()
            result.update({k: bots[k] for k in ('active', 'doneToday', 'bots')})
            # JSON escaping can expand arbitrary Unicode/control-like text. Keep
            # the actual default json.dumps wire representation below 480 bytes.
            while len(json.dumps(result).encode('utf-8')) >= 480 and result['bots']:
                result['bots'].pop()
            return result

    def summary(self):
        data = self.snapshot()
        return {**{k: data[k] for k in ('enabled', 'source', 'stale')},
                'botCount': self.store.snapshot()['botCount']}
