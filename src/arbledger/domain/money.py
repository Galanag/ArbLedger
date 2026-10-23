"""Currency-aware quantization utilities for simulated settlement amounts."""

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from .exceptions import ValidationError
from .validation import currency_code, decimal_value

# ISO-style minor-unit overrides commonly encountered in ledgers. Unlisted currencies
# default to two decimal places; deployments should inject a regulated currency table.
_MINOR_UNITS = {"JPY": 0, "KRW": 0, "CLP": 0, "BHD": 3, "KWD": 3, "OMR": 3, "BTC": 8, "ETH": 8}


def quantize_currency(amount: Decimal, currency: str) -> Decimal:
    """Round a monetary amount to the configured minor unit using banker's rounding.

    Excessive precision or magnitude raises a domain validation error rather than
    leaking ``decimal.InvalidOperation`` from an internal arithmetic operation.
    """
    amount = decimal_value(amount, field="monetary amount")
    code = currency_code(currency)
    places = _MINOR_UNITS.get(code, 2)
    quantum = Decimal(1).scaleb(-places)
    try:
        return amount.quantize(quantum, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        raise ValidationError("monetary amount exceeds supported Decimal precision") from exc
