"""Typed domain errors used to make rejected state transitions explicit."""


class ArbLedgerError(Exception):
    """Base class for expected application/domain failures."""


class ValidationError(ArbLedgerError, ValueError):
    """A value or command does not satisfy the domain contract."""


class QuoteValidationError(ValidationError):
    """A market quote is malformed or internally inconsistent."""


class StaleQuoteError(QuoteValidationError):
    """A quote arrived outside the configured event-time window."""


class OutOfOrderQuoteError(QuoteValidationError):
    """A venue stream regressed in timestamp or sequence number."""


class LedgerInvariantError(ArbLedgerError):
    """A journal would violate double-entry or account invariants."""


class InsufficientFundsError(LedgerInvariantError):
    """A posting would make a non-overdraft account balance negative."""


class AccountNotFoundError(LedgerInvariantError, KeyError):
    """A journal references an account that has not been registered."""


class DuplicateAccountError(LedgerInvariantError):
    """An account identifier is already registered."""


class IdempotencyConflictError(LedgerInvariantError):
    """An idempotency key was reused with a different transaction payload."""


class OpportunityExpiredError(ArbLedgerError):
    """An opportunity is no longer safe to execute under its validity policy."""


class PipelineStateError(ArbLedgerError):
    """A queue-pipeline operation is invalid for its current lifecycle state."""
