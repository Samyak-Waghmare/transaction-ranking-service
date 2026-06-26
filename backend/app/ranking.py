"""Multi-factor ranking (the leaderboard).

The score blends THREE independent signals so that no single behaviour can
dominate the board:

    1. VOLUME     — total value the user has generated.
    2. FREQUENCY  — how many distinct transactions they have made.
    3. RECENCY    — how recently they have been active.

FAIRNESS / ANTI-MANIPULATION DESIGN
------------------------------------
* Diminishing returns: VOLUME and FREQUENCY use a logarithm, so the marginal
  value of each extra dollar / transaction shrinks. One whale transaction or a
  burst of spam transactions cannot run away with the board.
* Normalisation: VOLUME and FREQUENCY are scaled by the current maximum, so
  the score is relative to the field and the components are comparable.
* Time decay: RECENCY uses half-life decay, so a user who stops participating
  steadily slides down — you cannot "win once and coast".
* Server-assigned timestamps (see store._now): clients cannot forge recency.
* Velocity limiting (see store._enforce_rate_limit): caps the spam that could
  inflate FREQUENCY in the first place.

The final score is a weighted sum of the three components and always lands in
[0, 1]. The weights are configurable and must sum to 1.0.
"""

from __future__ import annotations

import math

from . import config


def _volume_component(total_value: float) -> float:
    # log10(1 + x): 0 at x=0 and grows with strong diminishing returns.
    return math.log10(1.0 + max(total_value, 0.0))


def _frequency_component(transaction_count: int) -> float:
    return math.log10(1.0 + max(transaction_count, 0))


def _recency_factor(last_activity: float, now: float) -> float:
    """Half-life decay in [0, 1]. 1.0 = just active, → 0 as time passes."""
    days_idle = max(now - last_activity, 0.0) / 86_400.0
    return 0.5 ** (days_idle / config.RECENCY_HALF_LIFE_DAYS)


def compute_ranking(users: list[dict], now: float) -> list[dict]:
    """Rank a snapshot of users.

    ``users`` is a list of dicts with keys: userId, total_value,
    transaction_count, last_activity. Returns a list of ranking entries
    sorted best-first, each carrying the per-factor (weighted) breakdown.
    """
    if not users:
        return []

    raw = []
    for u in users:
        raw.append(
            {
                "userId": u["userId"],
                "total_value": u["total_value"],
                "transaction_count": u["transaction_count"],
                "last_activity": u["last_activity"],
                "volume": _volume_component(u["total_value"]),
                "frequency": _frequency_component(u["transaction_count"]),
                "recency": _recency_factor(u["last_activity"], now),
            }
        )

    # Normalise volume & frequency against the strongest user in the field.
    max_volume = max((r["volume"] for r in raw), default=0.0) or 1.0
    max_frequency = max((r["frequency"] for r in raw), default=0.0) or 1.0

    entries = []
    for r in raw:
        norm_volume = r["volume"] / max_volume
        norm_frequency = r["frequency"] / max_frequency
        recency = r["recency"]  # already absolute in [0, 1]

        # Weighted contributions — these three sum to the final score.
        w_volume = config.RANK_WEIGHT_VOLUME * norm_volume
        w_frequency = config.RANK_WEIGHT_FREQUENCY * norm_frequency
        w_recency = config.RANK_WEIGHT_RECENCY * recency
        score = w_volume + w_frequency + w_recency

        entries.append(
            {
                "userId": r["userId"],
                "score": round(score, 6),
                "totalValue": round(r["total_value"], 2),
                "transactionCount": r["transaction_count"],
                "last_activity": r["last_activity"],
                "breakdown": {
                    "volume": round(w_volume, 6),
                    "frequency": round(w_frequency, 6),
                    "recency": round(w_recency, 6),
                },
            }
        )

    # Deterministic ordering: score desc, then value desc, then most recent,
    # then userId asc — guarantees a stable, reproducible leaderboard.
    entries.sort(
        key=lambda e: (
            -e["score"],
            -e["totalValue"],
            -e["last_activity"],
            e["userId"],
        )
    )

    for i, e in enumerate(entries, start=1):
        e["rank"] = i

    return entries
