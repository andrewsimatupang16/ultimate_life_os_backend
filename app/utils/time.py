from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Asia/Jakarta"
DEFAULT_UTC_OFFSET_HOURS = 7
DEFAULT_TZINFO = timezone(timedelta(hours=DEFAULT_UTC_OFFSET_HOURS), DEFAULT_TIMEZONE)


def utc_now() -> datetime:
    """Return UTC as naive datetime to match the existing database columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc_naive(value: datetime | None) -> datetime | None:
    """Normalize a datetime to the system storage contract: UTC without tzinfo.

    API clients may send ISO datetimes either with timezone information
    (for example ``2026-06-28T03:00:00Z``) or without it. The database schema
    currently uses naive DateTime columns, so aware datetimes are converted to
    UTC and stripped of tzinfo before persistence. Naive datetimes are treated
    as already-normalized UTC values to avoid silently applying the server's
    local timezone.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def require_utc_naive(value: datetime, field_name: str) -> datetime:
    """Return a normalized datetime or raise a clear validation error."""
    normalized = to_utc_naive(value)
    if normalized is None:
        raise ValueError(f"{field_name} is required")
    return normalized


def attach_utc(value: datetime | None) -> datetime | None:
    """Interpret a stored naive datetime as UTC-aware for presentation logic."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fallback_tzinfo(timezone_name: str | None = None):
    if timezone_name in {"UTC", "Etc/UTC", "GMT", "Etc/GMT"}:
        return timezone.utc
    return DEFAULT_TZINFO


def get_tzinfo(timezone_name: str | None = None):
    """Return a usable tzinfo even on Windows without the tzdata package."""
    timezone_name = timezone_name or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name != DEFAULT_TIMEZONE:
            try:
                return ZoneInfo(DEFAULT_TIMEZONE)
            except ZoneInfoNotFoundError:
                pass
        return _fallback_tzinfo(timezone_name)


def user_timezone_name(user) -> str:
    timezone_name = getattr(user, "timezone", None) or DEFAULT_TIMEZONE
    try:
        ZoneInfo(timezone_name)
        return timezone_name
    except ZoneInfoNotFoundError:
        return DEFAULT_TIMEZONE


def user_tzinfo(user):
    timezone_name = getattr(user, "timezone", None) or DEFAULT_TIMEZONE
    return get_tzinfo(timezone_name)


def local_date_for_user(user, value: datetime | None = None) -> date:
    tz = user_tzinfo(user)
    value = attach_utc(value or utc_now())
    return value.astimezone(tz).date()


def local_day_bounds_utc(user, local_day: date) -> tuple[datetime, datetime]:
    """Return UTC-naive [start, end) bounds for a user's local calendar day."""
    tz = user_tzinfo(user)
    start_local = datetime.combine(local_day, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return to_utc_naive(start_local), to_utc_naive(end_local)


def local_period_bounds_utc(user, period: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    tz = user_tzinfo(user)
    now = attach_utc(now or utc_now())
    local_now = now.astimezone(tz)

    if period == "daily":
        start_local = datetime.combine(local_now.date(), time.min, tzinfo=tz)
        end_local = start_local + timedelta(days=1)
    elif period == "weekly":
        week_start_date = local_now.date() - timedelta(days=local_now.weekday())
        start_local = datetime.combine(week_start_date, time.min, tzinfo=tz)
        end_local = start_local + timedelta(days=7)
    elif period == "monthly":
        start_local = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start_local.month == 12:
            end_local = start_local.replace(year=start_local.year + 1, month=1)
        else:
            end_local = start_local.replace(month=start_local.month + 1)
    elif period == "yearly":
        start_local = local_now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local.replace(year=start_local.year + 1)
    else:
        raise ValueError("period must be daily, weekly, monthly, or yearly")

    return to_utc_naive(start_local), to_utc_naive(end_local)
