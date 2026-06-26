"""Thread-safe, in-memory data store.

WHY IN-MEMORY?
    The assignment allows in-memory/mock data as long as it is explained.
    Keeping state in-process lets this project demonstrate the *interesting*
    concerns — atomic updates, idempotency and concurrency control — directly
    and transparently, instead of delegating them to a database engine. The
    store is deliberately isolated behind a small API so it could be swapped
    for Redis/PostgreSQL with no change to the routes or ranking code.

CONCURRENCY MODEL
    FastAPI runs the (synchronous) route handlers in a worker thread pool, so
    two requests really can touch this state at the same time. Every operation
    that reads-then-writes shared state runs inside a single ``threading.RLock``
    critical section. That makes each operation atomic with respect to the
    others: no lost updates, no half-applied transactions, no torn reads.

CONSISTENCY MODEL
    Per-user aggregates (total value, count, timestamps) are updated in the
    same locked section that appends the transaction, so the aggregates can
    never disagree with the underlying transaction list.
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import config
from .errors import RateLimitExceededError, UserNotFoundError


def _now() -> float:
    """Authoritative server time (epoch seconds).

    Using server time — not a client-supplied timestamp — means clients
    cannot backdate or future-date transactions to manipulate the recency
    component of their ranking.
    """
    return datetime.now(timezone.utc).timestamp()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# Cap on how many individual transactions we retain per user for the summary
# response. Aggregates always reflect *all* transactions; only the returned
# detail list is trimmed to keep responses bounded.
_MAX_RETAINED_TX_PER_USER = 1000


@dataclass
class StoredTransaction:
    transactionId: str
    userId: str
    amount: float
    type: str
    timestamp: float

    def as_record(self) -> dict:
        return {
            "transactionId": self.transactionId,
            "amount": self.amount,
            "type": self.type,
            "timestamp": _iso(self.timestamp),
        }


@dataclass
class UserAccount:
    """Per-user aggregate state plus a bounded transaction history."""

    userId: str
    total_value: float = 0.0
    transaction_count: int = 0
    first_seen: float = field(default_factory=_now)
    last_activity: float = field(default_factory=_now)
    transactions: deque[StoredTransaction] = field(
        default_factory=lambda: deque(maxlen=_MAX_RETAINED_TX_PER_USER)
    )

    def apply(self, tx: StoredTransaction) -> None:
        """Atomically fold a new transaction into the aggregates.

        Called only from inside the store lock.
        """
        self.total_value = round(self.total_value + tx.amount, 2)
        self.transaction_count += 1
        self.last_activity = tx.timestamp
        self.transactions.append(tx)

    def summary(self) -> dict:
        avg = (
            round(self.total_value / self.transaction_count, 2)
            if self.transaction_count
            else 0.0
        )
        # Newest first, capped for response size.
        recent = list(self.transactions)[::-1][:50]
        return {
            "userId": self.userId,
            "totalValue": round(self.total_value, 2),
            "transactionCount": self.transaction_count,
            "averageTransaction": avg,
            "firstSeen": _iso(self.first_seen),
            "lastActivity": _iso(self.last_activity),
            "transactions": [t.as_record() for t in recent],
        }


class TransactionStore:
    """The single source of truth. All mutating paths hold ``self._lock``."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._users: dict[str, UserAccount] = {}
        # transactionId -> the StoredTransaction it produced (idempotency map).
        self._transactions: dict[str, StoredTransaction] = {}
        # userId -> sliding window of recent *new* transaction timestamps,
        # used for velocity-based abuse prevention.
        self._rate_windows: dict[str, deque[float]] = {}

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def record_transaction(
        self,
        userId: str,
        amount: float,
        tx_type: str,
        transactionId: str | None,
    ) -> tuple[dict, bool]:
        """Process a transaction exactly once.

        Returns ``(response_dict, duplicate)``. ``duplicate=True`` means the
        ``transactionId`` was already processed and the stored result is being
        replayed without double-counting.
        """
        with self._lock:
            # 1) IDEMPOTENCY — check this *before* anything else (including the
            #    rate limiter) so retries are cheap and never penalised.
            if transactionId is not None and transactionId in self._transactions:
                existing = self._transactions[transactionId]
                user = self._users[existing.userId]
                return (
                    self._build_tx_response(existing, user, duplicate=True),
                    True,
                )

            now = _now()

            # 2) ABUSE PREVENTION — velocity / rate limit per user. Only new
            #    (non-duplicate) transactions reach this point.
            self._enforce_rate_limit(userId, now)

            # 3) Assign a server-side id if the client did not provide one.
            tx_id = transactionId or f"srv-{uuid.uuid4()}"

            # Guard against an accidental collision on a generated id.
            while transactionId is None and tx_id in self._transactions:
                tx_id = f"srv-{uuid.uuid4()}"

            tx = StoredTransaction(
                transactionId=tx_id,
                userId=userId,
                amount=amount,
                type=tx_type,
                timestamp=now,
            )

            # 4) CONSISTENT UPDATE — create/update the user and index the
            #    transaction in the same critical section.
            user = self._users.get(userId)
            if user is None:
                user = UserAccount(userId=userId, first_seen=now, last_activity=now)
                self._users[userId] = user

            user.apply(tx)
            self._transactions[tx_id] = tx
            self._rate_windows.setdefault(userId, deque()).append(now)

            return self._build_tx_response(tx, user, duplicate=False), False

    def _enforce_rate_limit(self, userId: str, now: float) -> None:
        window = self._rate_windows.setdefault(userId, deque())
        cutoff = now - config.RATE_LIMIT_WINDOW_SECONDS
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= config.RATE_LIMIT_MAX_TX:
            retry_after = round(window[0] + config.RATE_LIMIT_WINDOW_SECONDS - now, 2)
            raise RateLimitExceededError(
                f"Rate limit exceeded: max {config.RATE_LIMIT_MAX_TX} "
                f"transactions per {config.RATE_LIMIT_WINDOW_SECONDS:g}s for user "
                f"'{userId}'.",
                details={"retryAfterSeconds": max(retry_after, 0.0)},
            )

    @staticmethod
    def _build_tx_response(
        tx: StoredTransaction, user: UserAccount, duplicate: bool
    ) -> dict:
        return {
            "status": "duplicate" if duplicate else "created",
            "duplicate": duplicate,
            "transactionId": tx.transactionId,
            "userId": tx.userId,
            "amount": tx.amount,
            "type": tx.type,
            "processedAt": _iso(tx.timestamp),
            "user": user.summary(),
            "message": (
                "Transaction already processed; returning the original result."
                if duplicate
                else "Transaction recorded successfully."
            ),
        }

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def get_summary(self, userId: str) -> dict:
        with self._lock:
            user = self._users.get(userId)
            if user is None:
                raise UserNotFoundError(f"No user found with id '{userId}'.")
            return user.summary()

    def snapshot_users(self) -> list[dict]:
        """Return a consistent point-in-time copy of all user aggregates.

        Copying inside the lock means ranking computes on a stable snapshot
        even while new transactions arrive.
        """
        with self._lock:
            return [
                {
                    "userId": u.userId,
                    "total_value": u.total_value,
                    "transaction_count": u.transaction_count,
                    "last_activity": u.last_activity,
                    "first_seen": u.first_seen,
                }
                for u in self._users.values()
            ]

    def stats(self) -> dict:
        with self._lock:
            return {
                "users": len(self._users),
                "transactions": len(self._transactions),
            }

    # ------------------------------------------------------------------
    # Demo helpers
    # ------------------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self._users.clear()
            self._transactions.clear()
            self._rate_windows.clear()


# Module-level singleton used by the routes.
store = TransactionStore()
