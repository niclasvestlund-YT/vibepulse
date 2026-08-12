"""Aggregate Max Tracker daily activity into streaks, windows and the v1 payload.

Ren funktionsyta: inget IO, ingen tidszonlogik. Varje funktion tar redan
lokaliserade datumsträngar ("YYYY-MM-DD") och räknar enbart på kalenderdatum
(:mod:`datetime.date`), aldrig på klockslag eller epoktid — DST-växlingar och
årsskiften (inklusive ISO-veckoår med 53 veckor) hanteras därför korrekt utan
någon särskild tidszonskod.

``build_payload`` konsumerar ett internt ``state``-dict som en framtida
``MaxTrackerStore`` (backfill/persistens) förväntas mata:

    state = {
        "claude": {
            "days": {"2026-08-01": {"pct": 55, "act": True, "vol": 4200}, ...},
            "weeks": {"2026-W31": True, "2026-W32": False, ...},
        },
        "codex": {"days": {...}, "weeks": {...}},
        "stale": False,  # optional, defaults to False
    }

``pct`` är kvot-procent för dagen (``None`` = ingen kvotmätning den dagen);
alltid ett heltal eller ``None`` — enhetens parser har ett int8_t-fält och
avvisar HELA svaret om en enda dag bär ett brutet tal, så avrundning sker
här, inte device-side (se ``_round_day_pct``). ``act`` är sant om det fanns
någon agentaktivitet den dagen (används för både den kombinerade
STREAK-räkningen och som förutsättning för att räkna ut ``lvl`` — se
``_provider_days``).

``lvl`` (tercilnivå 0-2) har TVÅ separata källpopulationer, aldrig blandade:
en dag med en RÅ ``vol`` (dagsvolym i tokens; blir aldrig en del av svaret,
bara underlag) rankas via :func:`volume_levels` mot alla andra ``vol``-
bärande dagar. En dag som istället bär ett explicit ``lvl``-fält direkt
(så här matar :class:`MaxTrackerStore` in en tidigare SPARAD, redan
klassificerad tercil efter en omstart) är AUKTORITATIV för sig själv —
den används verbatim och deltar aldrig i rankningen, varken som kandidat
eller som underlag för andras trösklar. Utan den uppdelningen skulle en
sparad tercil (som per designen aldrig kan backa till rå volym igen)
tyst räknas om varje gång den nya poolen av session-volymer ändras —
[0,1,2] blir [0,0,1] efter en enda spara/läs-cykel, permanent, eftersom
avslutade backfill-filer aldrig läses om.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

if __package__:
    from .codex_rollout import codex_rollout_rate_limits, observation_timestamp
else:  # direktkörning: python3 tools/tokenserver/max_tracker.py
    from codex_rollout import codex_rollout_rate_limits, observation_timestamp


PROVIDERS: tuple[str, ...] = ("claude", "codex")
WINDOW_WEEKS = 20
WINDOW_DAYS = WINDOW_WEEKS * 7
AGGREGATE_MAX = 999
PLAN_LABELS: dict[str, str] = {
    "pro": "PRO",
    "max5x": "MAX 5X",
    "max20x": "MAX 20X",
    "plus": "PLUS",
}


def volume_levels(day_volumes: dict[str, int]) -> dict[str, int]:
    """Bucket each day's raw volume into a tercile level 0..2.

    Thresholds are derived from the *distinct nonzero* volumes present
    (ties always land in the same bucket, since they share a rank). Days
    with a zero or falsy volume get level 0 without taking part in the
    threshold computation — there is nothing to compare them against, and
    zero is honestly the bottom of the range. A single distinct nonzero
    volume also resolves to level 0: with no second data point there is no
    basis to call it "high" or "medium", and the honesty rule in the design
    ("never fabricate") favors the conservative reading.

    Every key present in ``day_volumes`` gets an entry in the result.
    """
    distinct = sorted({volume for volume in day_volumes.values()
                        if volume and volume > 0})
    count = len(distinct)
    rank = {volume: (index * 3 // count) for index, volume in enumerate(distinct)}
    return {day: (rank[volume] if volume and volume > 0 else 0)
            for day, volume in day_volumes.items()}


def coding_streak(active_dates: set[str], today: str) -> int:
    """Consecutive calendar days of activity ending at ``today``.

    If ``today`` itself has no recorded activity yet, the streak is not
    presumed broken (the day isn't over) — counting instead starts from
    yesterday, so a live streak keeps showing until a full day genuinely
    passes without activity.
    """
    today_date = date.fromisoformat(today)
    cursor = today_date if today in active_dates else today_date - timedelta(days=1)
    streak = 0
    while cursor.isoformat() in active_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def week_key(date_str: str) -> str:
    """ISO week key, e.g. "2026-W32" (ISO week-year, not calendar year)."""
    iso_year, iso_week, _ = date.fromisoformat(date_str).isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _week_key_to_monday(key: str) -> date:
    year_str, week_str = key.split("-W")
    return date.fromisocalendar(int(year_str), int(week_str), 1)


def max_weeks_streak(week_maxed: dict[str, bool], this_week: str) -> int:
    """Consecutive COMPLETED ISO weeks maxed, ending at the most recent
    completed week (the week before ``this_week``).

    ``this_week`` itself is never inspected: a maxed-but-still-in-progress
    current week neither breaks nor extends the streak until it completes
    (becomes "the week before" on a later call).
    """
    cursor = _week_key_to_monday(this_week) - timedelta(days=7)
    streak = 0
    while week_maxed.get(week_key(cursor.isoformat())):
        streak += 1
        cursor -= timedelta(days=7)
    return streak


def _round_day_pct(pct: float) -> int:
    """Round a percent to the whole number the device parser requires.

    The wire contract's per-day ``pct`` is an int8_t field: a single
    fractional value (Claude's utilization arrives as e.g. 15.5 or 99.96
    "by construction") makes ``tk_max_tracker_parse`` reject the ENTIRE
    payload, not just that one day — the tracker pages would stay dark
    forever. Rounds half away from zero (never Python's banker's
    rounding), but a sub-100 observation is never rounded UP to the exact
    100 cell: only a truly observed ``100.0`` may ever render there,
    mirroring the presenter's 99/100 split (pct == 100 is reserved for an
    exact quota max, never an artifact of rounding 99.6).
    """
    if pct >= 100.0:
        return 100
    return min(int(math.floor(pct + 0.5)), 99)


def dense_window(today: str, weeks: int,
                  per_day: dict[str, dict]) -> list[list[int]]:
    """Render ``weeks`` ISO-Monday-aligned weeks (``weeks * 7`` entries).

    Index 0 is the Monday that starts the oldest week; the last index is
    the Sunday of the CURRENT ISO week (which may be after ``today`` --
    today is the last non-padded cell in the grid; any day after it
    within that same final week is padding, rendered exactly like an
    absent day). The grid is column-major by ISO week device-side, so day
    index 0 must fall on a Monday for the columns to line up — the window
    is therefore anchored to today's ISO week, not to a fixed 140-day
    lookback from today.

    Any day past ``today`` (the unwritten remainder of the current week)
    and any day absent from ``per_day`` both render as ``[-1, -1]``.
    ``per_day`` maps a date string to a dict with optional ``pct``/``lvl``
    keys; missing or ``None`` values become ``-1``. ``pct`` is coerced
    through :func:`_round_day_pct` here too -- a defensive backstop so
    this function can never emit a fractional pct even if some future
    caller's ``per_day`` bypasses the store's own rounding.
    """
    today_date = date.fromisoformat(today)
    current_monday = today_date - timedelta(days=today_date.isoweekday() - 1)
    window_start = current_monday - timedelta(days=7 * (weeks - 1))

    out: list[list[int]] = []
    for offset in range(weeks * 7):
        day = window_start + timedelta(days=offset)
        if day > today_date:
            out.append([-1, -1])
            continue
        record = per_day.get(day.isoformat())
        if not record:
            out.append([-1, -1])
            continue
        pct = record.get("pct")
        lvl = record.get("lvl")
        out.append([_round_day_pct(pct) if pct is not None else -1,
                    lvl if lvl is not None else -1])
    return out


def _window_week_keys(today: str, weeks: int) -> list[str]:
    today_date = date.fromisoformat(today)
    current_monday = today_date - timedelta(days=today_date.isoweekday() - 1)
    window_start = current_monday - timedelta(days=7 * (weeks - 1))
    return [week_key((window_start + timedelta(days=7 * index)).isoformat())
            for index in range(weeks)]


def _clamp_aggregate(value: int) -> int:
    return max(0, min(AGGREGATE_MAX, value))


def _provider_days(days: dict[str, dict]) -> dict[str, dict]:
    """Merge stored ``pct``/``act``/``vol``/``lvl`` per day into the honest
    ``pct``/``lvl`` pair the contract needs, over the FULL history handed
    in (aggregates and terciles both need everything the server has ever
    seen, not just the visible window).

    ``lvl`` is computed from volume terciles for every active day — even
    one that also carries a real ``pct`` (rule: gray backfill and quota
    color are independent fields, not mutually exclusive). ``lvl`` is only
    ``-1`` when the day was not active at all.

    Two separate populations feed ``lvl``, never mixed (see the module
    docstring): a day carrying a raw ``vol`` is RANKED via
    :func:`volume_levels` against every other ``vol``-carrying day. A day
    that instead already carries an explicit ``lvl`` (this is how
    :class:`MaxTrackerStore` reintroduces a previously-SAVED, already-
    classified tercile after a restart) is AUTHORITATIVE for itself: used
    verbatim, and excluded entirely from the ranking pool -- neither as a
    candidate nor as a threshold input for everyone else's terciles. A day
    can only ever supply one or the other, never both; ``vol`` wins by
    construction (:meth:`MaxTrackerStore.observe_volume` clears any
    inherited ``lvl`` the moment a day starts collecting real session
    volume again).
    """
    rankable = {day: (record.get("vol") or 0)
               for day, record in days.items()
               if record.get("act") and "lvl" not in record}
    levels = volume_levels(rankable)

    merged: dict[str, dict] = {}
    for day, record in days.items():
        pct = record.get("pct")
        active = bool(record.get("act"))
        if not active:
            lvl = -1
        elif "lvl" in record:
            lvl = record["lvl"]
        else:
            lvl = levels.get(day, -1)
        merged[day] = {
            "pct": pct if pct is not None else -1,
            "lvl": lvl,
        }
    return merged


def build_payload(state: dict, today: str,
                   plans: dict[str, str | None]) -> dict:
    """Build the GET /api/max-tracker v1 contract dict from ``state``.

    ``state`` holds ``{"claude": {"days": ..., "weeks": ...}, "codex": {...},
    "stale": bool}`` (see the module docstring). ``plans`` maps provider ->
    a raw plan flag value (or ``None``/missing); only allowlisted values in
    :data:`PLAN_LABELS` produce a ``planLabel`` field — anything else is
    simply omitted, never rendered as an invented label.

    All four aggregates (``codingStreakDays``, ``maxWeeksStreak``,
    ``maxWeeks``, ``maxDays``) are computed over the full history in
    ``state`` (not just the visible window) and clamped to
    :data:`AGGREGATE_MAX` — the device parser rejects larger values.
    ``avgPeakPct`` is the odd one out: it only ever averages real-``pct``
    days inside the visible window, so it stays meaningful as a "lately"
    number even once history runs long.
    """
    plans = plans or {}
    payload: dict = {
        "v": 1,
        "weeks": WINDOW_WEEKS,
        "stale": bool(state.get("stale", False)),
    }

    active_dates: set[str] = set()
    for provider in PROVIDERS:
        days = (state.get(provider) or {}).get("days") or {}
        active_dates.update(day for day, record in days.items()
                             if record.get("act"))
    payload["codingStreakDays"] = (
        None if not active_dates
        else _clamp_aggregate(coding_streak(active_dates, today)))

    window_week_keys = _window_week_keys(today, WINDOW_WEEKS)
    this_week = week_key(today)

    for provider in PROVIDERS:
        provider_state = state.get(provider) or {}
        days = provider_state.get("days") or {}
        weeks_maxed = provider_state.get("weeks") or {}
        merged_days = _provider_days(days)

        window_days = dense_window(today, WINDOW_WEEKS, merged_days)
        window_maxed = [1 if weeks_maxed.get(key) else 0
                         for key in window_week_keys]

        real_pct = [pair[0] for pair in window_days if pair[0] != -1]
        avg_peak_pct = (round(sum(real_pct) / len(real_pct), 1)
                         if real_pct else None)

        provider_payload: dict = {}
        plan_flag = plans.get(provider)
        label = (PLAN_LABELS.get(plan_flag)
                 if isinstance(plan_flag, str) else None)
        if label:
            provider_payload["planLabel"] = label
        provider_payload.update({
            "avgPeakPct": avg_peak_pct,
            "maxWeeksStreak": _clamp_aggregate(
                max_weeks_streak(weeks_maxed, this_week)),
            "maxWeeks": _clamp_aggregate(
                sum(1 for maxed in weeks_maxed.values() if maxed)),
            "maxDays": _clamp_aggregate(
                sum(1 for record in merged_days.values()
                    if record["pct"] == 100)),
            "weekMaxed": window_maxed,
            "days": window_days,
        })
        payload[provider] = provider_payload

    return payload


# ---------------------------------------------------------------------------
# MaxTrackerStore: backfill, ongoing rollup and persistence. Everything
# above this line is pure (no IO, no clock); everything below owns the
# durable ``state`` dict described in the module docstring plus the
# filesystem scans that fill it in.

def _current_month_start_ts() -> float:
    """Epoch seconds for the start of the current calendar month, local
    time -- the exact boundary tokenserver.py's own live volume scanner
    (``_compute``) already uses to decide which Claude files it owns
    (files older than this are skipped there without even being opened).
    ``MaxTrackerStore.backfill_step`` mirrors it exactly for Claude so the
    two channels -- the live scanner and the backfill thread -- never
    touch the same file's bytes; see the class docstring's single-writer
    note.
    """
    now = datetime.now().astimezone()
    return now.replace(day=1, hour=0, minute=0, second=0,
                       microsecond=0).timestamp()


def _local_date_str(ts: float) -> str:
    """Epoch seconds -> the Mac's local calendar date, "YYYY-MM-DD".

    Same day-boundary convention as the rest of tokenserver: the day is
    whichever one it is on this machine's clock, never UTC's.
    """
    return datetime.fromtimestamp(ts).astimezone().date().isoformat()


def _finite_pct(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 <= value <= 100)


def _finite_minutes(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


class MaxTrackerStore:
    """Owns Max Tracker's durable state: backfill, live rollup, persistence.

    Two independent channels feed the same per-provider ``days``/``weeks``
    state that :func:`build_payload` (via :meth:`snapshot`) renders:

    * **Backfill** (:meth:`backfill_step`) — a bounded, resumable forward
      scan of ``codex_root`` (``rollout-*.jsonl``, for historical quota-%
      peaks and week-maxed flags — Codex logs its own rate limits) and
      ``claude_root`` (``*.jsonl`` usage records, for historical activity +
      token volume only — Claude's quota-% is never reconstructible after
      the fact, so those days keep ``pct: None`` forever).
    * **Live rollup** (:meth:`observe_quota` / :meth:`observe_volume`) — the
      immediate-freshness path a running probe uses for *today*.

    ``observe_quota`` precondition (enforced by the CALLER, not here): every
    call must be a genuinely fresh, live observation. A stale/cached quota
    value must never reach this method — it would silently become a
    day's recorded peak with no way to tell it apart from a real one. Task
    6's endpoint wiring owns that filtering.

    Single-writer rule for Claude volume (``observe_volume``): tokenserver
    .py's own ``/api/tokens`` scanner (``_compute``) ALSO incrementally
    reads ``claude_root`` and calls ``observe_volume`` for every Claude
    file whose mtime falls in the current calendar month — that's the
    exact same tree :meth:`backfill_step` walks. Without a boundary, both
    channels would independently track their own byte offset into the
    same file and each add the same tokens, silently doubling every
    current-month day's volume. The split: :meth:`backfill_step` skips any
    Claude file whose mtime is in the current month outright (never
    stats its progress, never opens it) — that file is the live
    scanner's exclusive territory for as long as it keeps being touched.
    The moment a file goes quiet into a PRIOR month (its mtime no longer
    qualifies, exactly when the live scanner's own month filter also
    drops it), backfill picks it up for the first time and reads it whole
    in one pass — by construction never overlapping with what live already
    counted, and with no gap either, since backfill was always going to
    see it eventually once it stopped being "this month's business".

    Threading contract: this store is shared by design -- a background
    thread drives :meth:`backfill_step`/:meth:`observe_volume`/:meth:`save`,
    a probe thread calls :meth:`observe_quota`, and the HTTP thread calls
    :meth:`snapshot`, all concurrently. Every public method takes an
    internal re-entrant lock for its whole body (matching the repo's
    existing pattern of readers only ever touching a locked, internally
    consistent snapshot -- see agent_status.py/tokenserver.py), so no
    caller needs its own external locking. The lock is re-entrant because
    backfill's per-line handlers call the public :meth:`observe_volume`
    while :meth:`backfill_step` already holds it. :meth:`save` is the one
    exception to "whole body locked": only pruning + building the
    to-be-written payload happens under the lock; the actual disk write
    happens afterwards, unlocked, so a slow fsync never blocks a live
    probe or an HTTP snapshot.
    """

    RETENTION_DAYS = 400
    _GENERAL_WINDOW_MINUTES = 600  # Codex README rule: >600 min = the week
    _BLOCK_BYTES = 64 * 1024
    _MAX_RECORDS_PER_STEP = 256
    # The agent-status follower's 1 MiB line cap exists for its 0.5 s live
    # polling latency budget. Backfill is a different animal: a Mac-side
    # batch path with no latency deadline, reading real Codex/Claude
    # session files that have been observed with genuine, wanted lines up
    # to ~2 MiB (a real quota peak can live in one). Dropping those would
    # be worse than buffering a bounded amount more, so backfill gets its
    # own, larger ceiling: lines up to this size are parsed and counted in
    # full; only a line wider than this is skipped -- scanned past in
    # bounded 64 KiB blocks and discarded without ever being buffered
    # whole -- so a single line can never permanently stall a file's
    # backfill (see _drain_file).
    _MAX_LINE_BYTES = 8 * 1024 * 1024
    _SCHEMA_VERSION = 1

    def __init__(self, path: Path, codex_root: Path, claude_root: Path):
        self.path = Path(path)
        self.codex_root = Path(codex_root)
        self.claude_root = Path(claude_root)
        self._lock = threading.RLock()
        self._state = {provider: {"days": {}, "weeks": {}}
                       for provider in PROVIDERS}
        # Keyed by inode alone -- NEVER by path: a path under
        # ~/.claude/projects encodes the project name, which the privacy
        # contract forbids writing to disk. See _advance_one_file. Each
        # entry also carries the size/offset/discarding-line state needed
        # to resume a grown file correctly instead of rescanning it.
        self._backfill = {provider: {} for provider in PROVIDERS}
        # In-memory ONLY (never persisted -- raw log content must not
        # touch disk state): the not-yet-terminated bytes of whatever
        # line is currently being collected for a given inode, so a small
        # ``budget_bytes`` still makes guaranteed progress on a large line
        # over several backfill_step calls instead of re-reading the same
        # stuck bytes from disk every time. Lost on restart with zero risk
        # of data loss or double-counting -- the persisted offset in
        # ``_backfill`` only ever advances past a line once it has fully
        # resolved (parsed or discarded), so losing this cache just means
        # re-reading that one line's bytes from disk again.
        self._pending = {provider: {} for provider in PROVIDERS}
        self._load()

    # -- live rollup ---------------------------------------------------

    def observe_quota(self, provider: str, window_minutes: float | None,
                      pct: float, ts: float) -> None:
        """Roll one live quota observation into today's peak / this week.

        ``window_minutes`` classifies the observation exactly like the
        Codex README rule: absent/``None`` or <= 600 minutes is the primary
        (session) window and bumps ``date(ts)``'s recorded peak percent up
        to ``pct`` (never down -- only the day's maximum survives); a
        numeric value > 600 minutes is the general weekly window, which
        marks that ISO week maxed only when ``pct`` is exactly 100 (never
        un-marks it otherwise -- once maxed, a week stays maxed).

        Deliberately never marks a day active: this method is driven by a
        fixed-cadence background probe that fires whether or not the user
        did anything, so treating "we got a quota reading" as "coding
        happened" would fabricate a streak. Activity comes only from
        :meth:`observe_volume`.
        """
        if provider not in self._state or not _finite_pct(pct):
            return
        if not isinstance(ts, (int, float)) or isinstance(ts, bool) or not math.isfinite(ts):
            return
        date_str = _local_date_str(ts)
        with self._lock:
            bucket = self._state[provider]
            if window_minutes is None:
                self._bump_day_pct(bucket, date_str, pct)
            elif _finite_minutes(window_minutes):
                if window_minutes <= self._GENERAL_WINDOW_MINUTES:
                    self._bump_day_pct(bucket, date_str, pct)
                elif pct >= 100:
                    bucket["weeks"][week_key(date_str)] = True
            # else: garbage window_minutes -- ignore the observation
            # entirely, never guess which window it meant.

    @staticmethod
    def _bump_day_pct(bucket: dict, date_str: str, pct: float) -> None:
        day = bucket["days"].setdefault(date_str, {})
        rounded = _round_day_pct(float(pct))
        current = day.get("pct")
        if current is None or rounded > current:
            day["pct"] = rounded

    def observe_volume(self, provider: str, date_str: str, tokens: int) -> None:
        """Add ``tokens`` of raw activity to ``date_str`` and mark it active.

        Accumulates (does not overwrite): repeated calls for the same date
        add up, matching how usage log records are naturally summed as they
        are parsed. A zero (or invalid) amount is a pure no-op -- it never
        fabricates activity for a day nothing actually happened on.

        A day reloaded from disk carries an AUTHORITATIVE ``lvl`` (see the
        module docstring) instead of a raw ``vol`` -- the moment this day
        starts collecting real volume again this session, that loaded
        tercile is cleared: fresh raw numbers are always more trustworthy
        than a coarse snapshot from before the last restart, and a day
        must never carry both an authoritative ``lvl`` and a ``vol`` at
        the same time (ambiguous which one wins).
        """
        if provider not in self._state:
            return
        if (not isinstance(tokens, (int, float)) or isinstance(tokens, bool)
                or not math.isfinite(tokens) or tokens <= 0):
            return
        try:
            date.fromisoformat(date_str)
        except (TypeError, ValueError):
            return
        with self._lock:
            day = self._state[provider]["days"].setdefault(date_str, {})
            day["act"] = True
            day["vol"] = int(day.get("vol") or 0) + int(tokens)
            day.pop("lvl", None)

    # -- backfill --------------------------------------------------------

    def backfill_step(self, budget_bytes: int = 1_048_576) -> bool:
        """Drain one bounded slice of historical backfill work.

        Advances at most one not-yet-complete file per root (Codex, then
        Claude), each capped at ``budget_bytes`` (default 1 MiB) or 256
        records -- whichever comes first -- read in 64 KiB blocks. A file
        already fully drained at its current size is never reopened; one
        that has since GROWN (still being appended to) resumes from the
        offset already recorded for its inode rather than rescanning from
        the start, so already-counted volume is never double-counted. A
        line up to :data:`_MAX_LINE_BYTES` is always fully parsed and
        counted, even across several calls if ``budget_bytes`` is smaller
        than it; only a line wider than that cap is skipped -- scanned
        past without ever being buffered whole or endlessly retried from
        the same stuck point, so it can never permanently stall this
        file's backfill. Returns True while there is more work to drain
        (call again); False once both roots are fully caught up.

        Claude files with a mtime in the current calendar month are
        skipped entirely -- see the class docstring's single-writer rule:
        that is tokenserver.py's live ``/api/tokens`` scanner's exclusive
        territory, and touching it here would double-count every token it
        already counted.
        """
        with self._lock:
            codex_more = self._advance_one_file(
                "codex", self.codex_root, "**/rollout-*.jsonl",
                budget_bytes, self._handle_codex_event)
            claude_more = self._advance_one_file(
                "claude", self.claude_root, "**/*.jsonl",
                budget_bytes, self._handle_claude_event,
                live_channel_cutoff_ts=_current_month_start_ts())
            return codex_more or claude_more

    def _advance_one_file(self, provider: str, root: Path, pattern: str,
                          budget_bytes: int, handle_event,
                          live_channel_cutoff_ts: float | None = None) -> bool:
        if not root.is_dir():
            return False
        bucket = self._backfill[provider]
        pending = self._pending[provider]
        try:
            paths = sorted(root.glob(pattern))
        except OSError:
            return False

        def deferred_to_live(info) -> bool:
            return (live_channel_cutoff_ts is not None and
                    info.st_mtime >= live_channel_cutoff_ts)

        chosen = None
        for path in paths:
            try:
                info = path.stat()
            except OSError:
                continue
            if deferred_to_live(info):
                continue  # this month's own business -- not backfill's
            entry = bucket.get(info.st_ino)
            if self._is_fully_drained(entry, info):
                continue  # this inode was already drained at this size
            chosen = (path, info, entry)
            break
        if chosen is None:
            return False

        path, info, entry = chosen
        if entry is None or entry.get("size", 0) > info.st_size:
            # Brand-new inode, or the file at this inode got SMALLER since
            # we last looked (truncated/rewritten in place) -- not safe to
            # resume mid-file, so start over. Every downstream effect this
            # scan produces (day-peak = max, week-maxed = OR-in True,
            # volume = sum) stays correct under a full replay; only a
            # shrink can make a stored offset point past real content.
            start_offset = 0
            discarding = False
            pending.pop(info.st_ino, None)  # stale -- drop any cached bytes
        else:
            # Same inode, same or LARGER size (the normal "still being
            # appended to" case) -- resume exactly where the last call left
            # off instead of rescanning from 0, which would otherwise
            # double-count every already-drained record on each growth.
            # ``discarding`` carries forward whether we were still mid-way
            # through skipping an oversized line when we last stopped.
            start_offset = entry.get("offset", 0)
            discarding = bool(entry.get("discarding", False))
        pending_buf = pending.get(info.st_ino, b"")
        (new_offset, new_pending_buf, still_discarding,
         dangling_eof) = self._drain_file(
            path, start_offset, budget_bytes, handle_event, pending_buf,
            discarding)
        # Normally "done" just means we've read up to the file's current
        # size. The one exception is Finding 6: a final line that never
        # got a terminating newline (a writer that crashed or is still
        # mid-write) would otherwise leave new_offset permanently short of
        # info.st_size with nothing more this file can offer right now --
        # starving every file behind it forever. _drain_file signals that
        # case explicitly (dangling_eof) so it's treated as "done at this
        # size" instead: offset stays at the START of that unterminated
        # tail (nothing was counted for it), and _is_fully_drained already
        # reopens the moment the file's size actually changes, at which
        # point the (now-complete) line is parsed exactly once from there.
        done = dangling_eof or new_offset >= info.st_size
        bucket[info.st_ino] = {
            "offset": new_offset, "size": info.st_size, "done": done,
            "discarding": still_discarding}
        if new_pending_buf:
            pending[info.st_ino] = new_pending_buf
        else:
            pending.pop(info.st_ino, None)
        if not done:
            return True

        pending.pop(info.st_ino, None)  # finished -- nothing left to cache
        # This file just finished -- is any OTHER discovered file still
        # incomplete? (Cheap dict lookups only; re-stat'ing here is fine,
        # it never reads file content.) A file deferred to the live
        # channel is not "incomplete work" for backfill -- it's simply
        # not backfill's to report, or the background thread would mark
        # itself dirty and save needlessly on every tick for as long as
        # any file is actively being written this month.
        for other in paths:
            if other == path:
                continue
            try:
                other_info = other.stat()
            except OSError:
                continue
            if deferred_to_live(other_info):
                continue
            other_entry = bucket.get(other_info.st_ino)
            if not self._is_fully_drained(other_entry, other_info):
                return True
        return False

    @staticmethod
    def _is_fully_drained(entry, info) -> bool:
        return bool(entry and entry.get("done") and
                    entry.get("size") == info.st_size)

    def _drain_file(self, path: Path, start_offset: int, budget_bytes: int,
                    handle_event, pending_buf: bytes = b"",
                    discarding: bool = False):
        """Parse complete JSON lines from ``start_offset`` forward, up to
        ``budget_bytes`` (or 256 records), reading in 64 KiB blocks.

        ``start_offset`` is always a SAFE, already-resolved boundary --
        right after the last line that was fully parsed or fully
        discarded -- and it only ever moves forward from there once
        another line resolves; it is not touched by mere in-progress
        reading. ``pending_buf`` is the in-memory-only cache (see
        ``self._pending`` in :meth:`__init__`) of bytes already read past
        ``start_offset`` for the line currently being collected but not
        yet resolved -- reading resumes right after it, so those bytes
        are never re-fetched from disk within one process's lifetime, but
        losing it (a restart) costs at most a redundant re-read up to
        :data:`_MAX_LINE_BYTES`, never data loss or a double count, since
        ``start_offset`` itself never advanced past it.

        A line up to :data:`_MAX_LINE_BYTES` is buffered whole and, once
        its terminator is found, parsed and counted normally -- backfill
        must not drop real quota/volume data just because a line happens
        to be a couple of megabytes. Only past that cap does a line get
        SKIPPED rather than buffered: bytes are scanned forward in 64 KiB
        blocks looking only for the next newline, nothing is held in
        memory for it, and -- because that content is being thrown away
        regardless of what happens next -- ``start_offset`` advances
        immediately as each block is confirmed garbage, not just once the
        terminator is finally found. That is what guarantees a line wider
        than even a single step's entire budget still finishes over
        several calls instead of restarting the same failed scan from
        the same stuck point every time.

        Every iteration tries to resolve a line ALREADY sitting in
        ``buf`` before ever touching the file again -- only once ``buf``
        has no complete line left (and isn't already known-oversized) do
        we read more. This is what makes the record-cap handoff safe: if
        the 256-record cap is hit while ``buf`` still holds complete,
        already-read lines (common when many small records land in one
        block), those lines are carried forward in ``new_pending_buf``
        and are the very first thing the NEXT call resolves -- without
        that ordering, a call that starts by seeking to (and finding
        nothing past) the exact byte those cached lines already cover
        would treat that as EOF and make zero progress, forever.

        Returns ``(new_offset, new_pending_buf, still_discarding,
        dangling_eof)``. ``dangling_eof`` is True only when we've
        confirmed true end-of-file while a NON-oversized, NON-terminated
        line remains in ``buf`` -- Finding 6's "writer never finished
        this line" case. In that case ``new_pending_buf`` is always
        empty: the caller is expected to treat the file as done at its
        current size (see :meth:`_advance_one_file`) rather than carry
        the dangling fragment forward.
        """
        offset = start_offset
        seek_position = start_offset + (0 if discarding else len(pending_buf))
        records_seen = 0
        bytes_read = 0
        buf = b"" if discarding else pending_buf
        hit_eof = False
        try:
            with path.open("rb") as source:
                source.seek(seek_position)
                while records_seen < self._MAX_RECORDS_PER_STEP:
                    if discarding:
                        if bytes_read >= budget_bytes:
                            break
                        chunk = source.read(
                            min(self._BLOCK_BYTES, budget_bytes - bytes_read))
                        if not chunk:
                            hit_eof = True
                            break
                        bytes_read += len(chunk)
                        newline = chunk.find(b"\n")
                        if newline < 0:
                            # Pure garbage we're throwing away regardless
                            # -- safe to advance the persisted offset past
                            # it right now, not just once the terminator
                            # eventually turns up.
                            offset += len(chunk)
                            continue
                        discarding = False
                        records_seen += 1
                        offset += newline + 1
                        buf = chunk[newline + 1:]
                        continue

                    newline = buf.find(b"\n")
                    if newline >= 0:
                        # Resolve straight from buf -- no disk read needed.
                        raw_line, buf = buf[:newline], buf[newline + 1:]
                        offset += newline + 1
                        records_seen += 1
                        if raw_line.strip():
                            try:
                                event = json.loads(raw_line)
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                event = None
                            if event is not None:
                                handle_event(event)
                        continue

                    if len(buf) > self._MAX_LINE_BYTES:
                        # Only now, having crossed the cap, do we commit to
                        # discarding -- everything collected so far is
                        # unrecoverable garbage either way, so it's safe
                        # to mark it as consumed.
                        discarding = True
                        offset += len(buf)
                        buf = b""
                        continue

                    if bytes_read >= budget_bytes:
                        break
                    chunk = source.read(
                        min(self._BLOCK_BYTES, budget_bytes - bytes_read))
                    if not chunk:
                        hit_eof = True
                        break
                    bytes_read += len(chunk)
                    buf += chunk
        except OSError:
            return start_offset, pending_buf, discarding, False

        if hit_eof and not discarding and buf:
            # Finding 6: confirmed end-of-file with a genuinely
            # unterminated tail (never oversized, never resolved). Not
            # "budget ran out, more to read next time" -- there IS no
            # more, right now. Count nothing for it and let the caller
            # mark the file done at its current size so it stops blocking
            # every file behind it; a later regrowth naturally reopens it
            # (see _is_fully_drained) and this exact tail parses cleanly
            # from `offset`, which was never advanced past it.
            return offset, b"", False, True
        return offset, (b"" if discarding else buf), discarding, False

    def _handle_codex_event(self, event) -> None:
        rate_limits = codex_rollout_rate_limits(event)
        if rate_limits is None:
            return
        limit_name = rate_limits.get("limit_name")
        if limit_name is not None:
            if not isinstance(limit_name, str) or limit_name:
                return  # a named/scoped quota, not the plan's own general one
        observed_at = (event.get("timestamp")
                      if isinstance(event, dict) else None)
        observed_at = observation_timestamp(observed_at)
        if observed_at is None:
            return
        date_str = _local_date_str(observed_at)

        found_any = False
        for key in ("primary", "secondary"):
            parsed = self._backfill_window(rate_limits.get(key))
            if parsed is None:
                continue
            found_any = True
            pct, window_minutes = parsed
            if window_minutes <= self._GENERAL_WINDOW_MINUTES:
                self._bump_day_pct(self._state["codex"], date_str, pct)
            elif pct >= 100:
                self._state["codex"]["weeks"][week_key(date_str)] = True
        if found_any:
            # A genuine rollout rate-limit snapshot is real evidence Codex
            # was running that day -- unlike observe_quota's fixed-cadence
            # probe, this only happens when a session actually produced one.
            self._state["codex"]["days"].setdefault(
                date_str, {})["act"] = True

    @staticmethod
    def _backfill_window(window):
        """Validate one rate-limit window for historical backfill.

        Deliberately does not check ``resets_at`` against "now" (unlike the
        live probe's window validation) -- a backfilled window's reset time
        is essentially always long past by the time we read it, and that
        says nothing about whether the historical percent/window it
        recorded was real.

        The returned percent is already rounded through
        :func:`_round_day_pct` -- both of its consumers in
        :meth:`_handle_codex_event` need that: the day-peak path stores it
        as-is, and the week-maxed path's ``pct >= 100`` check relies on
        the SAME "never rounds a sub-100 observation up to 100" guarantee
        (a 99.96 reading must never falsely mark a week maxed, exactly as
        it must never render as the exact-red day cell).
        """
        if not isinstance(window, dict):
            return None
        pct = window.get("used_percent")
        window_minutes = window.get("window_minutes")
        if not _finite_pct(pct) or not _finite_minutes(window_minutes):
            return None
        return _round_day_pct(float(pct)), window_minutes

    def _handle_claude_event(self, event) -> None:
        """Feed one ``~/.claude/projects`` usage record into the day's
        activity/volume total. Unlike tokenserver.py's own ``/api/tokens``
        aggregate, this intentionally does not dedup resumed-session
        message/request ids: the result only ever becomes a coarse 0-2
        tercile level (:func:`volume_levels`), never a displayed number, so
        an occasional duplicate cannot change what the user sees.
        """
        if not isinstance(event, dict):
            return
        usage = (event.get("message") or {}).get("usage")
        ts_raw = event.get("timestamp")
        if not usage or not isinstance(usage, dict) or not ts_raw:
            return
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            return
        tokens = (
            (usage.get("input_tokens") or 0)
            + (usage.get("output_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0))
        if not isinstance(tokens, (int, float)) or tokens <= 0:
            return
        self.observe_volume("claude", ts.astimezone().date().isoformat(),
                            tokens)

    # -- output ------------------------------------------------------------

    def snapshot(self, today: str, plans: dict) -> dict:
        """Build the v1 Max Tracker contract dict for ``today``/``plans``.

        A thin wrapper around :func:`build_payload`: feeds it this store's
        FULL in-memory history (every aggregate needs the whole thing, not
        just the visible window -- see the module docstring). ``stale`` is
        always reported False here; this store has no notion of "the
        server failed to refresh" to surface, so a future caller that needs
        that must layer it on top of the returned dict.

        Each day forwards EITHER its raw ``vol`` (session-tracked, ranked
        by :func:`build_payload`) OR its authoritative loaded ``lvl``
        (verbatim, never ranked) -- never both; see the module docstring's
        two-population rule.
        """
        with self._lock:
            state = {
                provider: {
                    "days": {
                        day: {
                            "pct": record.get("pct"),
                            "act": bool(record.get("act")),
                            **({"lvl": record["lvl"]} if "lvl" in record
                               else {"vol": record.get("vol") or 0}),
                        }
                        for day, record in
                        self._state[provider]["days"].items()
                    },
                    "weeks": dict(self._state[provider]["weeks"]),
                }
                for provider in PROVIDERS
            }
        state["stale"] = False
        return build_payload(state, today, plans)

    # -- persistence ---------------------------------------------------

    def _prune_retention(self, today: str | None = None) -> None:
        anchor = date.today() if today is None else date.fromisoformat(today)
        cutoff = anchor - timedelta(days=self.RETENTION_DAYS)
        week_cutoff = cutoff - timedelta(days=7)
        for provider in PROVIDERS:
            days = self._state[provider]["days"]
            for day in list(days):
                try:
                    stale = date.fromisoformat(day) < cutoff
                except ValueError:
                    stale = True  # corrupt key -- drop it defensively
                if stale:
                    del days[day]
            weeks = self._state[provider]["weeks"]
            for week in list(weeks):
                try:
                    monday = _week_key_to_monday(week)
                except (ValueError, IndexError):
                    del weeks[week]
                    continue
                if monday < week_cutoff:
                    del weeks[week]

    def _load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        with self._lock:
            for provider in PROVIDERS:
                section = payload.get(provider)
                if isinstance(section, dict):
                    self._load_provider(provider, section)

    def _load_provider(self, provider: str, section: dict) -> None:
        """Populate ``self._state`` from one provider's persisted section.

        A loaded day's ``lvl`` (if any) becomes its AUTHORITATIVE tercile
        -- stored as ``lvl``, never reinterpreted as a raw ``vol`` to be
        re-ranked. Re-ranking a value that has already BEEN ranked once
        (and can never regain its original raw magnitude, since raw
        volume is deliberately never persisted -- see the module
        docstring) would reshuffle it against whatever different pool of
        in-session volumes happens to exist on this run, silently
        corrupting it a little more on every save/load cycle.
        """
        days_in = section.get("days")
        if isinstance(days_in, dict):
            days_out = self._state[provider]["days"]
            for day, record in days_in.items():
                try:
                    date.fromisoformat(day)
                except (TypeError, ValueError):
                    continue
                if not isinstance(record, dict):
                    continue
                pct = record.get("pct")
                act = bool(record.get("act"))
                lvl = record.get("lvl")
                out = {"act": act}
                if _finite_pct(pct):
                    out["pct"] = _round_day_pct(float(pct))
                if act and isinstance(lvl, int) and not isinstance(lvl, bool):
                    out["lvl"] = max(0, min(2, lvl))
                days_out[day] = out

        weeks_in = section.get("weeks")
        if isinstance(weeks_in, dict):
            weeks_out = self._state[provider]["weeks"]
            for week, maxed in weeks_in.items():
                if maxed:
                    weeks_out[week] = True

        backfill_in = section.get("backfill")
        if isinstance(backfill_in, dict):
            bucket = self._backfill[provider]
            for key, entry in backfill_in.items():
                inode = self._parse_backfill_key(key)
                if inode is None or not isinstance(entry, dict):
                    continue
                offset = entry.get("offset")
                size = entry.get("size")
                done = entry.get("done")
                discarding = entry.get("discarding", False)
                if (isinstance(offset, int) and not isinstance(offset, bool)
                        and offset >= 0 and
                        isinstance(size, int) and not isinstance(size, bool)
                        and size >= 0 and isinstance(done, bool)
                        and isinstance(discarding, bool)):
                    bucket[inode] = {
                        "offset": offset, "size": size, "done": done,
                        "discarding": discarding}

    @staticmethod
    def _parse_backfill_key(key):
        if not isinstance(key, str) or not key.isdigit():
            return None
        return int(key)

    def save(self, today: str | None = None) -> None:
        """Persist state atomically: write a sibling ``.tmp``, then
        ``os.replace`` it into place, mode 0600. Prunes anything older than
        :data:`RETENTION_DAYS` first, anchored on ``today`` (an ISO date
        string) -- omit it to anchor on the real current date; tests pass
        an explicit value so the retention boundary is exact and
        independent of wall-clock timing. Never serializes anything beyond
        the ``{v, days: {pct, act, lvl}, weeks: bool, backfill}`` allowlist
        per provider -- no prompts, commands, projects, models, file names
        or raw log events, ever.

        Only pruning and building the payload dict happen under the lock;
        the payload is a plain, independent value at that point, so the
        actual (potentially slow) disk write runs unlocked and never
        blocks a concurrent probe or HTTP snapshot.
        """
        with self._lock:
            self._prune_retention(today)
            payload = {provider: self._provider_payload(provider)
                      for provider in PROVIDERS}
        self._atomic_write(payload)

    def _provider_payload(self, provider: str) -> dict:
        """Build one provider's persisted section.

        Same two-population split as :func:`_provider_days` (see the
        module docstring): only days carrying a raw ``vol`` this session
        are ranked; a day that already carries an authoritative ``lvl``
        (loaded from a previous save, never topped up with fresh volume
        since) is written back VERBATIM, so it stays stable across any
        number of save/load cycles instead of drifting a little further
        every time.
        """
        bucket = self._state[provider]
        rankable = {day: (record.get("vol") or 0)
                   for day, record in bucket["days"].items()
                   if record.get("act") and "lvl" not in record}
        levels = volume_levels(rankable)

        days_out = {}
        for day, record in bucket["days"].items():
            act = bool(record.get("act"))
            pct = record.get("pct")
            if not act:
                lvl_out = None
            elif "lvl" in record:
                lvl_out = record["lvl"]
            else:
                lvl_out = levels.get(day, 0)
            days_out[day] = {
                "pct": _round_day_pct(float(pct)) if pct is not None else None,
                "act": act,
                "lvl": lvl_out,
            }
        weeks_out = {week: True for week, maxed in bucket["weeks"].items()
                    if maxed}
        backfill_out = {
            str(inode): {"offset": entry["offset"], "size": entry["size"],
                        "done": bool(entry["done"]),
                        "discarding": bool(entry.get("discarding", False))}
            for inode, entry in self._backfill[provider].items()
        }
        return {"v": self._SCHEMA_VERSION, "days": days_out,
               "weeks": weeks_out, "backfill": backfill_out}

    def _atomic_write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.path.parent,
                    prefix=f".{self.path.name}.", suffix=".tmp",
                    delete=False) as stream:
                temp_path = Path(stream.name)
                os.chmod(temp_path, 0o600)
                json.dump(payload, stream, ensure_ascii=False,
                         separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
            temp_path = None
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError:
                    pass
