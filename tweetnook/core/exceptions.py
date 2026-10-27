"""Project-specific exceptions."""

from __future__ import annotations


class TweetNookError(RuntimeError):
    """Base exception for the project."""


class RepeatedFocalAbsenceError(TweetNookError):
    """Raised when consecutive TweetDetail responses omit their requested focal tweet."""


class ConfigError(TweetNookError):
    """Raised for local configuration issues."""


class AuthResolutionError(ConfigError):
    """Raised when local auth inputs cannot be resolved."""


class QueryIdRefreshError(TweetNookError):
    """Raised when query IDs cannot be refreshed or resolved."""


class APIResponseError(TweetNookError):
    """Raised when Twitter/X returns an unexpected API response."""

    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class AuthExpiredError(APIResponseError):
    """Raised for expired or rejected credentials."""


class FeatureFlagDriftError(APIResponseError):
    """Raised when a request likely fails because feature flags drifted."""


class RateLimitExhaustedError(APIResponseError):
    """Raised when retry and cooldown logic still ends in 429."""


class StaleQueryIdError(APIResponseError):
    """Raised when a query ID refresh still cannot satisfy an operation."""


class ProcessLockError(TweetNookError):
    """Raised when another sync already holds the process lock."""


class ArchiveOwnerMismatchError(TweetNookError):
    """Raised when a local archive owner differs from the resolved Twitter/X account."""
