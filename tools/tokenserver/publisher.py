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
30-second cadence would burn 2 880 for just one endpoint.  Content hashes
alone are insufficient because honest presentation fields such as ``at`` and
reset countdowns change continuously.  Every endpoint therefore has a hard
minimum send interval as well as change detection.  The ceilings keep two
continuously changing publishers below the account-wide free allowance.
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

# Check locally at the same rhythm the panel polls.  Cloud sends have the
# per-endpoint ceilings below; the base heartbeat remains five minutes.
CHECK_EVERY_S = 30.0
HEARTBEAT_EVERY_S = 300.0

# KV's free allowance is account-wide: 1,000 writes/day.  At these absolute
# ceilings one publisher can make at most 384 writes/day (288 quota updates,
# 48 Max Tracker updates and 48 GitHub updates); two make at most 768.
# The local 30-second check remains responsive without being allowed to turn
# volatile presentation timestamps into cloud writes.
MIN_SEND_INTERVAL_S = {
    "/api/tokens": 300.0,
    "/api/max-tracker": 1800.0,
    "/api/github": 1800.0,
}

# An honest User-Agent.  The relay is our own mailbox; there is nobody to
# imitate and no reason to.
USER_AGENT = "vibepulse-publisher/1"


def payload_fingerprint(payload) -> str:
    """A stable content hash for change detection.

    sort_keys makes dict ordering irrelevant; the serialized form is also
    exactly what gets sent, so "fingerprint unchanged" always means "the
    mailbox already holds these bytes".
    """
    body = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(body).hexdigest()


def should_send(last_fingerprint, last_sent_at, fingerprint, now,
                min_interval_s=0.0) -> bool:
    """The write-economy rule: send on change, or on heartbeat, never else.

    ``last_fingerprint is None`` means never sent (fresh start): send.
    A failed send must NOT update the caller's state, so the next tick
    retries by construction rather than by a retry mechanism.
    """
    if last_fingerprint is None:
        return True
    elapsed = now - last_sent_at
    if elapsed < min_interval_s:
        return False
    if fingerprint != last_fingerprint:
        return True
    return elapsed >= max(HEARTBEAT_EVERY_S, min_interval_s)


class Publisher:
    """Owns the publish loop for a set of payload producers.

    ``producers`` maps endpoint path -> zero-arg callable returning the
    payload (the same callables the HTTP handlers use, so the mailbox can
    never diverge from what the LAN serves).  ``post`` is injectable for
    tests; the default does a real HTTP POST.
    """

    def __init__(self, relay_url: str, machine: str, producers: dict,
                 post=None, clock=time.time):
        self.relay_url = relay_url.rstrip("/")
        self.machine = machine
        self.producers = producers
        self.post = post if post is not None else self._http_post
        self.clock = clock
        # per path: (fingerprint, sent_at)
        self._state = {}
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
            min_interval = MIN_SEND_INTERVAL_S.get(path, HEARTBEAT_EVERY_S)
            if not should_send(last_fingerprint, sent_at, fingerprint, now,
                               min_interval):
                continue
            body = json.dumps(payload, sort_keys=True).encode()
            if self.post(self.relay_url + path, body):
                self._state[path] = (fingerprint, now)
                sends += 1
            else:
                # State untouched: next tick retries.  Log once per failure
                # tick is acceptable at this cadence; the panel keeps its
                # last good values either way.
                log.warning("publicering: %s nådde inte reläet", path)
        return sends

    def start(self):
        def run():
            # The first producer pass can scan large local histories.  Keep
            # it off the startup thread so the LAN server can bind at once.
            self.publish_once()
            while not self._stop.wait(CHECK_EVERY_S):
                self.publish_once()
        # Start the first pass immediately, but asynchronously: the mailbox
        # still warms without waiting one cadence and local startup stays
        # independent of producer cost or relay latency.
        self._thread = threading.Thread(target=run, name="relay-publisher",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
