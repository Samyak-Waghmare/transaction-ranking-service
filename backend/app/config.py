"""Central configuration for the backend service.

Every "magic number" lives here so that the validation rules, abuse
limits, and ranking weights are easy to find, reason about, and tune.
Values can be overridden via environment variables, which makes the same
code safe to run locally and in a deployed environment.
"""

from __future__ import annotations

import os


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# --------------------------------------------------------------------------
# Transaction validation bounds
# --------------------------------------------------------------------------
# A transaction represents a positive, value-generating event for a user
# (e.g. a purchase, a referral, a completed task). Amounts must be strictly
# positive and capped to a sane maximum to reject obviously bogus input.
MIN_TRANSACTION_AMOUNT: float = _env_float("MIN_TRANSACTION_AMOUNT", 0.01)
MAX_TRANSACTION_AMOUNT: float = _env_float("MAX_TRANSACTION_AMOUNT", 1_000_000.0)

# userId charset / length guard. Keeps ids predictable and log-safe.
USER_ID_PATTERN: str = r"^[A-Za-z0-9_.\-]{1,64}$"

# Optional transaction "type" (category of activity). Free-form types are
# rejected so the data stays clean and analysable.
ALLOWED_TRANSACTION_TYPES: tuple[str, ...] = (
    "purchase",
    "referral",
    "bonus",
    "review",
    "task",
    "generic",
)
DEFAULT_TRANSACTION_TYPE: str = "generic"

# --------------------------------------------------------------------------
# Abuse / manipulation prevention
# --------------------------------------------------------------------------
# Velocity limit: a single user may submit at most RATE_LIMIT_MAX_TX *new*
# transactions per RATE_LIMIT_WINDOW_SECONDS. Idempotent retries of an
# already-seen transaction do NOT count against this limit.
RATE_LIMIT_MAX_TX: int = _env_int("RATE_LIMIT_MAX_TX", 20)
RATE_LIMIT_WINDOW_SECONDS: float = _env_float("RATE_LIMIT_WINDOW_SECONDS", 10.0)

# --------------------------------------------------------------------------
# Ranking weights (see app.ranking for the full formula).
# These three weights MUST sum to 1.0 so the final score stays in [0, 1].
# --------------------------------------------------------------------------
RANK_WEIGHT_VOLUME: float = _env_float("RANK_WEIGHT_VOLUME", 0.50)
RANK_WEIGHT_FREQUENCY: float = _env_float("RANK_WEIGHT_FREQUENCY", 0.20)
RANK_WEIGHT_RECENCY: float = _env_float("RANK_WEIGHT_RECENCY", 0.30)

# Recency uses exponential (half-life) decay. After RECENCY_HALF_LIFE_DAYS of
# inactivity a user's recency factor halves, so nobody can sit at the top of
# the board forever without staying active.
RECENCY_HALF_LIFE_DAYS: float = _env_float("RECENCY_HALF_LIFE_DAYS", 7.0)

# --------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------
# Comma-separated list of allowed CORS origins. "*" allows any origin, which
# is convenient for a public demo frontend hosted on a different domain.
CORS_ALLOW_ORIGINS: str = os.getenv("CORS_ALLOW_ORIGINS", "*")

# Enables the /demo/* helper endpoints (seed + reset). On by default so the
# live demo and reviewers can easily reset state; set to "0" to disable.
ENABLE_DEMO_ENDPOINTS: bool = os.getenv("ENABLE_DEMO_ENDPOINTS", "1") != "0"


def validate_config() -> None:
    """Fail fast on a clearly broken configuration."""
    weight_sum = RANK_WEIGHT_VOLUME + RANK_WEIGHT_FREQUENCY + RANK_WEIGHT_RECENCY
    if abs(weight_sum - 1.0) > 1e-6:
        raise ValueError(
            "Ranking weights must sum to 1.0, got "
            f"{weight_sum:.4f} (volume={RANK_WEIGHT_VOLUME}, "
            f"frequency={RANK_WEIGHT_FREQUENCY}, recency={RANK_WEIGHT_RECENCY})"
        )
    if MIN_TRANSACTION_AMOUNT <= 0:
        raise ValueError("MIN_TRANSACTION_AMOUNT must be > 0")
    if MAX_TRANSACTION_AMOUNT <= MIN_TRANSACTION_AMOUNT:
        raise ValueError("MAX_TRANSACTION_AMOUNT must be greater than the minimum")
    if RECENCY_HALF_LIFE_DAYS <= 0:
        raise ValueError("RECENCY_HALF_LIFE_DAYS must be > 0")
