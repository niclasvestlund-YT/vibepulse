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

``pct`` är kvot-procent för dagen (``None`` = ingen kvotmätning den dagen).
``act`` är sant om det fanns någon agentaktivitet den dagen (används för både
den kombinerade STREAK-räkningen och som förutsättning för att räkna ut
``lvl`` — se ``_provider_days``). ``vol`` är den råa dagsvolymen (tokens);
den blir aldrig en del av svaret, bara underlag för tercil-nivån.
"""

from __future__ import annotations

from datetime import date, timedelta


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


def dense_window(today: str, weeks: int,
                  per_day: dict[str, dict]) -> list[list[int]]:
    """Render ``weeks`` ISO-Monday-aligned weeks (``weeks * 7`` entries).

    Index 0 is the Monday that starts the oldest week; the last index is
    the Sunday of the CURRENT ISO week (which may be after ``today``). The
    grid is column-major by ISO week device-side, so day index 0 must fall
    on a Monday for the columns to line up — the window is therefore
    anchored to today's ISO week, not to a fixed 140-day lookback from
    today.

    Any day past ``today`` (the unwritten remainder of the current week)
    and any day absent from ``per_day`` both render as ``[-1, -1]``.
    ``per_day`` maps a date string to a dict with optional ``pct``/``lvl``
    keys; missing or ``None`` values become ``-1``.
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
        out.append([pct if pct is not None else -1,
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
    """Merge stored ``pct``/``act``/``vol`` per day into the honest
    ``pct``/``lvl`` pair the contract needs, over the FULL history handed
    in (aggregates and terciles both need everything the server has ever
    seen, not just the visible window).

    ``lvl`` is computed from volume terciles for every active day — even
    one that also carries a real ``pct`` (rule: gray backfill and quota
    color are independent fields, not mutually exclusive). ``lvl`` is only
    ``-1`` when the day was not active at all.
    """
    volumes = {day: (record.get("vol") or 0)
               for day, record in days.items() if record.get("act")}
    levels = volume_levels(volumes)

    merged: dict[str, dict] = {}
    for day, record in days.items():
        pct = record.get("pct")
        active = bool(record.get("act"))
        merged[day] = {
            "pct": pct if pct is not None else -1,
            "lvl": levels.get(day, -1) if active else -1,
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
