"""Strict parsing and boundary validation helpers.

Decimal floats are rejected rather than silently importing their binary expansion.
Callers should pass decimal strings, integers, or :class:`decimal.Decimal` values.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .exceptions import ValidationError

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


def decimal_value(value: Any, *, field: str = "value") -> Decimal:
    """Convert an exact decimal input to ``Decimal``.

    Args:
        value: A ``Decimal``, integer, or decimal string. Floats and booleans are
            forbidden because they can introduce precision errors in financial math.
        field: Name displayed in validation errors.

    Raises:
        ValidationError: If conversion is unsafe or the value is not finite.

    Complexity: O(n), where n is the number of characters in a string input.
    """
    if isinstance(value, bool) or isinstance(value, float):
        raise ValidationError(f"{field} must be Decimal, int, or decimal string; float is forbidden")
    if not isinstance(value, (Decimal, int, str)):
        raise ValidationError(f"{field} must be Decimal, int, or decimal string")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{field} is not a valid decimal") from exc
    if not parsed.is_finite():
        raise ValidationError(f"{field} must be finite")
    return parsed


def currency_code(value: str) -> str:
    """Return a normalized ISO-4217-shaped currency token.

    The shape is validated without a static currency allowlist so future or local
    currencies can be represented. Currency validity against a banking directory is
    an adapter/integration responsibility.
    """
    if not isinstance(value, str):
        raise ValidationError("currency must be a string")
    normalized = value.strip().upper()
    if not _CURRENCY_RE.fullmatch(normalized):
        raise ValidationError(f"currency must be a three-letter code: {value!r}")
    return normalized


def identifier(value: str, *, field: str = "identifier") -> str:
    """Validate an account, venue, reference, or idempotency identifier."""
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValidationError(f"{field} must be 1-64 safe ASCII characters")
    return value


def aware_datetime(value: datetime, *, field: str = "timestamp") -> datetime:
    """Require an aware datetime and normalize it to UTC."""
    if not isinstance(value, datetime):
        raise ValidationError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)
