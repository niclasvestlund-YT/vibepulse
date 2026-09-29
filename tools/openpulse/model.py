"""USD contracts. None means unknown; a null upstream limit means unlimited."""
import math
from datetime import datetime, timezone


def number(value, *, signed=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and abs(value) <= 1e12 and (signed or value >= 0) else None


def normalize_key(data):
    result = {name: number(data.get(source)) for name, source in {
        "day": "usage_daily", "week": "usage_weekly", "month": "usage_monthly",
        "total": "usage", "byokDay": "byok_usage_daily",
        "byokWeek": "byok_usage_weekly", "byokMonth": "byok_usage_monthly",
    }.items()}
    limit = number(data.get("limit"))
    result.update(limit=limit,
                  limitState=("unlimited" if "limit" in data and data["limit"] is None
                              else "limited" if limit is not None else "unknown"),
                  limitRemaining=number(data.get("limit_remaining"), signed=True),
                  limitReset=data.get("limit_reset") if data.get("limit_reset") in
                  ("daily", "weekly", "monthly") else None,
                  limitIncludesByok=data.get("include_byok_in_limit") if
                  isinstance(data.get("include_byok_in_limit"), bool) else None)
    if result["limitState"] != "limited":
        result["limitRemaining"] = None
    return result


def normalize_credits(data):
    purchased, used = number(data.get("total_credits")), number(data.get("total_usage"))
    return {"accountBalance": purchased - used if purchased is not None and used is not None else None}


def periods(timestamp):
    dt = datetime.fromtimestamp(timestamp, timezone.utc)
    return dt.date(), dt.isocalendar()[:2], (dt.year, dt.month)


def key_view(values, observed, now, config, error=None, age=None):
    result = dict(values or normalize_key({}))
    age = max(0, now - observed) if age is None and observed is not None else age
    state = "error" if error else "no_data" if observed is None else "stale" if age >= 180 else "fresh"
    # Never carry yesterday's daily figure into today's labelled period.
    if observed is not None:
        for i, field in enumerate(("day", "week", "month")):
            if periods(observed)[i] != periods(now)[i]:
                result[field] = result["byok" + field.title()] = None
                if state == "fresh":
                    state = "stale"
    if not error and all(result[key] is None for key in ("day", "week", "month")) and result["limitState"] == "unknown":
        state = "no_data"
    budget = config["monthly_budget_usd"]
    ratio = result["month"] / budget if result["month"] is not None and budget else None
    result.update(schema=1, currency="USD", timezone="UTC", name=config["name"],
                  state=state, error=error, ageSeconds=age,
                  updatedAt=datetime.fromtimestamp(observed, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                  if observed is not None else None,
                  budget=budget, budgetPercent=ratio * 100 if ratio is not None else None,
                  budgetLevel=("unknown" if ratio is None else "critical" if ratio >= config["critical_at"]
                               else "warning" if ratio >= config["warning_at"] else "normal"))
    return result
