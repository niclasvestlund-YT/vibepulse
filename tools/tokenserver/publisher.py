"""Push the served numbers to a relay mailbox on the internet.

The panel could only fetch from this service when both sat on the same
LAN.  A guest network's client isolation, an IoT VLAN, or a host that
changed IP was enough to blank the glass while internet worked fine
(2026-08-17, the whole evening).  The relay is the answer's second half:
the panel-side failover landed in ``components/torget_net``; this module
is the service side, POSTing the same three payloads the LAN endpoints
serve to a mailbox the panel can always reach.

What crosses, and what never does, mirrors the firmware's tested boundary
(``test/test_relay_boundary.py``): numbers only.  ``/api/tokens``,
``/api/max-tracker`` and ``/api/github``.  Agent status and Needs You
carry project names, question text and commands — they are not published,
and there is deliberately no way to add them here without also changing
the firmware's boundary test.

Several machines may publish to the same mailbox (a Mac that sleeps, an
always-on PC).  Each send names its publisher so the mailbox can merge
freshest-per-source; this module only needs to be honest about who it is.

Write economy is a design constraint, not an optimization: the free tier
of the intended mailbox (Cloudflare KV) allows 1 000 writes/day, and a
30-second cadence would burn 2 880.  So a payload is sent only when its
content actually changed, plus a heartbeat so the mailbox's staleness is
bounded even when nothing changes, plus a floor and a daily budget that
turn "should stay inside the tier" into "cannot leave it".  Every rule
lives in a pure function or a token bucket the tests can hold still; the
arithmetic is spelled out at ``DAILY_WRITE_BUDGET`` below.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("tokenserver")

# --------------------------------------------------------------- economy --
# Cloudflare KV's free tier is not one allowance but two small ones and one
# large one: 100 000 reads/day, and only 1 000 writes/day and 1 000 LIST
# operations/day (their price sheet bills write, delete and list in a single
# class).  The read path no longer lists at all -- tools/relay/worker.js
# keeps a per-endpoint publisher index instead -- so the whole small bucket
# is this module's to spend, and it has to stay inside it by construction
# rather than by hope.  It did not, on 2026-08-21: half the day's allowance
# was gone by breakfast.  See docs/lessons.md.
#
# Three rules, each bounded arithmetic rather than a guess:
#
#   1. Fingerprints ignore the fields that are pure functions of the clock
#      (see DERIVED_SUFFIXES).  A countdown in minutes ticks by itself once
#      a minute, which alone forced >= 1 440 writes/day of nothing changing.
#      The mailbox ages those countdowns at READ time now, so the glass is
#      exact however seldom we speak.
#   2. Every endpoint has a floor between sends, and a heartbeat above it.
#      86 400/floor is that endpoint's hard ceiling for the day.
#   3. A daily budget across all endpoints is the backstop: whatever the
#      payloads do, one publisher cannot exceed DAILY_WRITE_BUDGET.
#
# Per publisher, that is:
#
#   endpoint            floor  ceiling/day   heartbeat  quiet/day
#   /api/tokens          180 s     480         900 s        96
#   /api/max-tracker     600 s     144        1800 s        48
#   /api/github          600 s     144        1800 s        48
#                                  ---                     ---
#                                  768                     192
#
# So 192 writes on a day where nothing at all happens, 768 in the worst
# case the floors permit, and DAILY_WRITE_BUDGET is where it actually
# stops.  Two machines on one mailbox is 800 of the 1 000 -- the
# two-publisher setup the README advertises, still with headroom.  A third
# machine does not fit any arithmetic on the free tier; lower the budget on
# each or move to the paid plan.
CHECK_EVERY_S = 30.0

HEARTBEAT_EVERY_S = 900.0
MIN_SEND_INTERVAL_S = 180.0

# path -> (heartbeat, floor).  Max Tracker is day-granular and the GitHub
# payload carries no clock at all, so neither needs the tokens cadence.
ENDPOINT_CADENCE = {
    "/api/tokens": (HEARTBEAT_EVERY_S, MIN_SEND_INTERVAL_S),
    "/api/max-tracker": (1800.0, 600.0),
    "/api/github": (1800.0, 600.0),
}

DAILY_WRITE_BUDGET = 400
BUDGET_BURST = 8

# Field suffixes whose value is a pure function of the current time, and so
# says nothing about whether the numbers moved.  "*ResetMin" is minutes left
# until a quota window rolls; "*ObservedAt" is when a probe last looked.
# Both still TRAVEL in every body that gets sent -- they are simply not a
# reason to send one.  The contract is flat by design (the glance pattern),
# so matching top-level keys is the whole rule.
DERIVED_SUFFIXES = ("ResetMin", "ObservedAt")

# An honest User-Agent.  The relay is our own mailbox; there is nobody to
# imitate and no reason to.
USER_AGENT = "vibepulse-publisher/1"


def stable_payload(payload):
    """The payload minus the fields that tick on their own.

    What is left is "the numbers", and only a change in those is worth a
    write.  Dropping a key here never drops it from the wire: the body that
    goes out is always the full, current payload.
    """
    if not isinstance(payload, dict):
        return payload
    return {key: value for key, value in payload.items()
            if not key.endswith(DERIVED_SUFFIXES)}


def payload_fingerprint(payload) -> str:
    """A stable content hash for change detection.

    sort_keys makes dict ordering irrelevant.  The hash covers
    :func:`stable_payload`, not the wire bytes, so "fingerprint unchanged"
    means "no number moved" -- which is the question the write economy
    actually asks.  A countdown that is one minute shorter than last tick is
    not news, and paying a KV write for it emptied the free tier by lunch.
    """
    body = json.dumps(stable_payload(payload), sort_keys=True).encode()
    return hashlib.sha256(body).hexdigest()


SEND_START = "start"
SEND_HEARTBEAT = "heartbeat"
SEND_CHANGE = "change"


def send_reason(last_fingerprint, last_sent_at, fingerprint, now,
                heartbeat=HEARTBEAT_EVERY_S, floor=MIN_SEND_INTERVAL_S):
    """Why this payload should go out now, or ``None`` for "it should not".

    ``last_fingerprint is None`` means never sent (fresh start): send.
    A failed send must NOT update the caller's state, so the next tick
    retries by construction rather than by a retry mechanism.

    The floor is what makes an endpoint's daily cost knowable: a payload
    that changes every 30 s (token volume does, while you work) would
    otherwise publish every 30 s.  A change during the floor is not lost --
    it goes out on the first tick after the floor lifts, carrying whatever
    the numbers say by then, which is the fresher answer anyway.

    The caller needs the reason and not just a yes, because the budget
    treats the two differently: see :meth:`Publisher.publish_once`.
    """
    if last_fingerprint is None:
        return SEND_START
    since = now - last_sent_at
    if since >= heartbeat:
        return SEND_HEARTBEAT
    if fingerprint != last_fingerprint and since >= floor:
        return SEND_CHANGE
    return None


def should_send(last_fingerprint, last_sent_at, fingerprint, now,
                heartbeat=HEARTBEAT_EVERY_S,
                floor=MIN_SEND_INTERVAL_S) -> bool:
    """The write-economy rule as a yes/no: change (no faster than ``floor``)
    or ``heartbeat``, never else."""
    return send_reason(last_fingerprint, last_sent_at, fingerprint, now,
                       heartbeat, floor) is not None


class WriteBudget:
    """A token bucket over the mailbox's daily write allowance.

    The floors above bound each endpoint; this bounds the publisher.  It
    refills at ``per_day`` writes per 24 h and holds ``burst`` of them, so a
    flurry of real changes still goes out promptly and a day that never
    stops changing simply slows down instead of running the account into
    429s.

    Time comes from the caller (the Publisher's injected clock), so the
    tests can hold a day still.  A clock that jumps backwards refills
    nothing rather than draining anything.
    """

    def __init__(self, per_day=DAILY_WRITE_BUDGET, burst=BUDGET_BURST):
        self.per_day = float(per_day)
        self.burst = float(burst)
        self._tokens = float(burst)
        self._filled_at = None

    def _refill(self, now) -> None:
        if self._filled_at is None:
            self._filled_at = now
            return
        elapsed = now - self._filled_at
        self._filled_at = now
        if elapsed <= 0:
            return
        self._tokens = min(self.burst,
                           self._tokens + elapsed * self.per_day / 86400.0)

    def allows(self, now) -> bool:
        """Is there a write to spend right now?  Spends nothing."""
        self._refill(now)
        return self._tokens >= 1.0

    def spend(self, now, forced=False) -> bool:
        """Take one write.  False when the bucket is empty.

        ``forced`` takes it anyway and lets the bucket go negative.  That is
        for the heartbeats: they are bounded by construction (192/day for
        the whole table, well under a day's refill), so overdrawing on them
        can never run away -- it just makes the next change-driven send wait
        for the bucket to climb back.  Without it, a busy /api/tokens spends
        every token the moment it is minted and the two slow endpoints never
        publish at all.  The overdraft stops at one burst so a mistuned
        table cannot lock changes out for days.
        """
        if not self.allows(now) and not forced:
            return False
        self._tokens = max(self._tokens - 1.0, -self.burst)
        return True


class Publisher:
    """Owns the publish loop for a set of payload producers.

    ``producers`` maps endpoint path -> zero-arg callable returning the
    payload (the same callables the HTTP handlers use, so the mailbox can
    never diverge from what the LAN serves).  ``post`` is injectable for
    tests; the default does a real HTTP POST.
    """

    def __init__(self, relay_url: str, machine: str, producers: dict,
                 post=None, clock=time.time, budget=None):
        self.relay_url = relay_url.rstrip("/")
        self.machine = machine
        self.producers = producers
        self.post = post if post is not None else self._http_post
        self.clock = clock
        self.budget = WriteBudget() if budget is None else budget
        # per path: (fingerprint, sent_at)
        self._state = {}
        self._throttle_logged_at = None
        self._stop = threading.Event()
        self._thread = None

    # ------------------------------------------------------------------ http

    def _http_post(self, url: str, body: bytes) -> bool:
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "X-VibePulse-Publisher": self.machine,
            })
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    # ------------------------------------------------------------------ loop

    def publish_once(self) -> int:
        """One pass over every producer.  Returns the number of sends.

        A producer that raises is logged and skipped -- one broken payload
        must never stop the others from publishing (the same isolation the
        HTTP handlers give each endpoint).
        """
        sends = 0
        for path, produce in self.producers.items():
            try:
                payload = produce()
            except Exception:
                log.exception("publicering: %s-producenten föll", path)
                continue
            fingerprint = payload_fingerprint(payload)
            last_fingerprint, sent_at = self._state.get(path, (None, 0.0))
            now = self.clock()
            heartbeat, floor = ENDPOINT_CADENCE.get(
                path, (HEARTBEAT_EVERY_S, MIN_SEND_INTERVAL_S))
            reason = send_reason(last_fingerprint, sent_at, fingerprint, now,
                                 heartbeat, floor)
            if reason is None:
                continue
            # Only CHANGE-driven sends wait for the budget.  A heartbeat is
            # what keeps Max Tracker and the GitHub pulse from going stale
            # in the mailbox for a whole working day while a busy
            # /api/tokens spends every token the moment it is minted -- and
            # the heartbeats' own total is fixed by the table above, so
            # letting them through can never be the thing that overruns the
            # tier.  The budget is checked BEFORE the post and drawn only
            # after a successful one: a send that never reached KV cost no
            # write and must not cost a token either.
            if reason == SEND_CHANGE and not self.budget.allows(now):
                self._note_throttled(path, now)
                continue
            body = json.dumps(payload, sort_keys=True).encode()
            if self.post(self.relay_url + path, body):
                self.budget.spend(now, forced=reason != SEND_CHANGE)
                self._state[path] = (fingerprint, now)
                sends += 1
            else:
                # State untouched: next tick retries.  Log once per failure
                # tick is acceptable at this cadence; the panel keeps its
                # last good values either way.
                log.warning("publicering: %s nådde inte reläet", path)
        return sends

    def _note_throttled(self, path, now) -> None:
        """One line an hour, not one per tick.

        Once the budget bites it bites on every tick -- 120 times an hour --
        and a log that says so 120 times has told you nothing the first line
        did not.
        """
        if (self._throttle_logged_at is not None and
                now - self._throttle_logged_at < 3600.0):
            return
        self._throttle_logged_at = now
        log.info("publicering: dagsbudgeten (%d skrivningar) är slut för "
                 "stunden — %s väntar på påfyllning", int(self.budget.per_day),
                 path)

    def start(self):
        def run():
            while not self._stop.wait(CHECK_EVERY_S):
                self.publish_once()
        # First pass immediately: the mailbox should be warm within one
        # cadence of service start, not one heartbeat.
        self.publish_once()
        self._thread = threading.Thread(target=run, name="relay-publisher",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
