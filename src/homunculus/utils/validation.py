import re
from zoneinfo import available_timezones

_E164_PATTERN = re.compile(r"^\+[1-9]\d{1,14}$")
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

VALID_TIMEZONES: frozenset[str] = frozenset(available_timezones())


def validate_phone(value: str) -> str:
    """Validate E.164 phone number format (+<country><number>, 2-15 digits)."""
    value = value.strip()
    if not _E164_PATTERN.match(value):
        msg = f"Invalid phone number: {value!r}. Must be E.164 format (e.g. +14155551234)."
        raise ValueError(msg)
    return value


def validate_email(value: str) -> str:
    """Validate basic email format (local@domain.tld)."""
    value = value.strip()
    if not _EMAIL_PATTERN.match(value):
        msg = f"Invalid email: {value!r}. Must be a valid email address."
        raise ValueError(msg)
    return value


def validate_timezone(value: str) -> str:
    """Validate timezone against IANA timezone database."""
    value = value.strip()
    if value not in VALID_TIMEZONES:
        msg = f"Invalid timezone: {value!r}. Use a valid IANA timezone (e.g. America/New_York)."
        raise ValueError(msg)
    return value
