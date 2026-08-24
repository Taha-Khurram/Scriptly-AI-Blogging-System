"""
Date and Timezone Utilities

Provides timezone-aware date formatting for the site.
Uses Python's built-in zoneinfo module (Python 3.9+).

``utcnow()`` and ``ensure_aware()`` exist because the codebase previously mixed
``datetime.utcnow()`` -- which returns a *naive* datetime that merely happens to
hold UTC -- with the timezone-aware values Firestore returns. Comparing the two
raises ``TypeError``, and the workaround in use was ``.replace(tzinfo=None)``,
which does not convert a timestamp to UTC: it discards the offset, so a value
submitted as 10:00+05:00 was treated as 10:00 UTC, five hours off. Every "now"
in the application goes through :func:`utcnow`, and anything arriving from
outside goes through :func:`ensure_aware`, so the two kinds can never meet.
``datetime.utcnow()`` is also deprecated from Python 3.12.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def utcnow():
    """The current instant as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def ensure_aware(value, assume=timezone.utc):
    """Return ``value`` as a timezone-aware datetime, or ``None``.

    A naive input is *assumed* to be in ``assume`` and stamped, rather than
    converted -- the only safe reading when the origin did not record an
    offset. Accepts an ISO-8601 string so values read back from JSON or
    Firestore do not each need their own parsing dance at the call site.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=assume)
    return value


def to_utc(value):
    """Convert ``value`` to UTC, *preserving the instant it names*.

    The distinction that matters: ``astimezone`` moves 10:00+05:00 to
    05:00+00:00 (same moment), whereas ``replace(tzinfo=utc)`` would call it
    10:00 UTC (a different moment, five hours later).
    """
    aware = ensure_aware(value)
    return aware.astimezone(timezone.utc) if aware else None

# Common timezones for dropdown selection
COMMON_TIMEZONES = [
    ('UTC', 'UTC'),
    ('America/New_York', 'Eastern Time (US)'),
    ('America/Chicago', 'Central Time (US)'),
    ('America/Denver', 'Mountain Time (US)'),
    ('America/Los_Angeles', 'Pacific Time (US)'),
    ('America/Toronto', 'Toronto (EST)'),
    ('America/Vancouver', 'Vancouver (PST)'),
    ('America/Sao_Paulo', 'Sao Paulo (BRT)'),
    ('Europe/London', 'London (GMT/BST)'),
    ('Europe/Paris', 'Paris (CET)'),
    ('Europe/Berlin', 'Berlin (CET)'),
    ('Europe/Moscow', 'Moscow (MSK)'),
    ('Asia/Dubai', 'Dubai (GST)'),
    ('Asia/Karachi', 'Pakistan (PKT)'),
    ('Asia/Kolkata', 'India (IST)'),
    ('Asia/Bangkok', 'Bangkok (ICT)'),
    ('Asia/Singapore', 'Singapore (SGT)'),
    ('Asia/Shanghai', 'China (CST)'),
    ('Asia/Tokyo', 'Japan (JST)'),
    ('Asia/Seoul', 'Korea (KST)'),
    ('Australia/Sydney', 'Sydney (AEST)'),
    ('Australia/Melbourne', 'Melbourne (AEST)'),
    ('Pacific/Auckland', 'Auckland (NZST)'),
]

# Date format options with examples
DATE_FORMATS = [
    ('MMM DD, YYYY', 'Jan 15, 2024'),
    ('DD/MM/YYYY', '15/01/2024'),
    ('MM/DD/YYYY', '01/15/2024'),
    ('YYYY-MM-DD', '2024-01-15'),
    ('DD MMM YYYY', '15 Jan 2024'),
    ('MMMM DD, YYYY', 'January 15, 2024'),
]

# Time format options
TIME_FORMATS = [
    ('12h', '2:30 PM'),
    ('24h', '14:30'),
]

# Locale/Language options
LOCALES = [
    ('en', 'English'),
    ('es', 'Spanish'),
    ('fr', 'French'),
    ('de', 'German'),
    ('pt', 'Portuguese'),
    ('it', 'Italian'),
    ('nl', 'Dutch'),
    ('ru', 'Russian'),
    ('zh', 'Chinese'),
    ('ja', 'Japanese'),
    ('ko', 'Korean'),
    ('ar', 'Arabic'),
    ('hi', 'Hindi'),
    ('ur', 'Urdu'),
]


def convert_to_timezone(dt, timezone_str='UTC'):
    """
    Convert a datetime to the specified timezone.

    Args:
        dt: datetime object (naive or aware)
        timezone_str: Timezone string (e.g., 'America/New_York')

    Returns:
        Timezone-aware datetime
    """
    if dt is None:
        return None

    try:
        tz = ZoneInfo(timezone_str)

        # If datetime is naive, assume it's UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo('UTC'))

        return dt.astimezone(tz)
    except Exception:
        return dt


def format_date(dt, date_format='MMM DD, YYYY', timezone='UTC'):
    """
    Format a datetime with the specified date format and timezone.

    Args:
        dt: datetime object
        date_format: Format string (e.g., 'MMM DD, YYYY', 'DD/MM/YYYY')
        timezone: Timezone string

    Returns:
        Formatted date string
    """
    if dt is None:
        return ''

    try:
        # Convert to timezone
        dt = convert_to_timezone(dt, timezone)

        # Format based on pattern
        format_map = {
            'MMM DD, YYYY': '%b %d, %Y',      # Jan 15, 2024
            'DD/MM/YYYY': '%d/%m/%Y',          # 15/01/2024
            'MM/DD/YYYY': '%m/%d/%Y',          # 01/15/2024
            'YYYY-MM-DD': '%Y-%m-%d',          # 2024-01-15
            'DD MMM YYYY': '%d %b %Y',         # 15 Jan 2024
            'MMMM DD, YYYY': '%B %d, %Y',      # January 15, 2024
        }

        strftime_format = format_map.get(date_format, '%b %d, %Y')
        return dt.strftime(strftime_format)
    except Exception:
        return str(dt)


def format_time(dt, time_format='12h', timezone='UTC'):
    """
    Format a datetime's time component.

    Args:
        dt: datetime object
        time_format: '12h' or '24h'
        timezone: Timezone string

    Returns:
        Formatted time string
    """
    if dt is None:
        return ''

    try:
        dt = convert_to_timezone(dt, timezone)

        if time_format == '24h':
            return dt.strftime('%H:%M')  # 14:30
        else:
            return dt.strftime('%I:%M %p').lstrip('0')  # 2:30 PM
    except Exception:
        return str(dt)


def format_datetime(dt, date_format='MMM DD, YYYY', time_format='12h', timezone='UTC'):
    """
    Format a full datetime with date and time.

    Args:
        dt: datetime object
        date_format: Date format string
        time_format: '12h' or '24h'
        timezone: Timezone string

    Returns:
        Formatted datetime string
    """
    if dt is None:
        return ''

    date_str = format_date(dt, date_format, timezone)
    time_str = format_time(dt, time_format, timezone)

    return f"{date_str} at {time_str}"


def get_current_time_preview(timezone='UTC', date_format='MMM DD, YYYY', time_format='12h'):
    """
    Get a preview of the current time in the specified format.
    Useful for the admin settings preview.

    Returns:
        Dict with formatted date and time strings
    """
    now = utcnow()

    return {
        'date': format_date(now, date_format, timezone),
        'time': format_time(now, time_format, timezone),
        'full': format_datetime(now, date_format, time_format, timezone),
    }
