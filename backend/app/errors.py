"""Domain-specific exceptions.

These are translated into structured JSON error responses by the handlers
registered in ``app.main``. Keeping them separate from HTTP concerns keeps
the store/ranking logic framework-agnostic and easy to unit test.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for expected, client-facing errors."""

    status_code: int = 400
    code: str = "bad_request"

    def __init__(self, message: str, details: object | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class UserNotFoundError(AppError):
    status_code = 404
    code = "user_not_found"


class RateLimitExceededError(AppError):
    status_code = 429
    code = "rate_limit_exceeded"
