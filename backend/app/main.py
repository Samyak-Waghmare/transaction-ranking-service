"""FastAPI application: routes, middleware and error handling.

Endpoints
---------
    POST /transaction        record a transaction (idempotent, rate-limited)
    GET  /summary/{userId}   per-user aggregate summary
    GET  /ranking            multi-factor leaderboard

    GET  /health             liveness/readiness probe
    GET  /                   service info
    POST /demo/seed          populate sample data (demo only)
    POST /demo/reset         clear all data (demo only)

Interactive API docs are served at /docs (Swagger UI) and /redoc.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Path, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, config
from .errors import AppError
from .models import (
    ErrorResponse,
    HealthResponse,
    RankingResponse,
    TransactionRequest,
    TransactionResponse,
    UserSummary,
)
from .ranking import compute_ranking
from .store import store

config.validate_config()

app = FastAPI(
    title="Transaction & Ranking Service",
    description=(
        "A small backend that records user transactions, exposes per-user "
        "summaries, and produces a fair, multi-factor ranking. Demonstrates "
        "validation, idempotency, concurrency-safe updates and "
        "abuse prevention."
    ),
    version=__version__,
)

# CORS so a frontend hosted on a different origin can call the API.
_origins = (
    ["*"]
    if config.CORS_ALLOW_ORIGINS.strip() == "*"
    else [o.strip() for o in config.CORS_ALLOW_ORIGINS.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================================================
# Error handling — every error returns the same { "error": {...} } shape.
# ==========================================================================
def _error_payload(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


@app.exception_handler(AppError)
async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    headers = {}
    if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS and isinstance(
        exc.details, dict
    ):
        retry = exc.details.get("retryAfterSeconds")
        if retry is not None:
            # Round up to whole seconds for the standard header.
            headers["Retry-After"] = str(max(int(retry), 0) + 1)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(exc.code, exc.message, exc.details),
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def _handle_validation_error(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    # Flatten Pydantic's error list into something friendly.
    details = [
        {
            "field": ".".join(str(p) for p in err.get("loc", []) if p != "body"),
            "message": err.get("msg"),
        }
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_payload(
            "validation_error", "Request validation failed.", details
        ),
    )


@app.exception_handler(Exception)
async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    # Last-resort handler: never leak a stack trace to the client.
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_payload("internal_error", "An unexpected error occurred."),
    )


# ==========================================================================
# Routes
# ==========================================================================
@app.get("/", tags=["meta"])
def root() -> dict:
    return {
        "service": "Transaction & Ranking Service",
        "version": __version__,
        "docs": "/docs",
        "endpoints": ["POST /transaction", "GET /summary/{userId}", "GET /ranking"],
    }


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    stats = store.stats()
    return HealthResponse(
        status="ok",
        version=__version__,
        users=stats["users"],
        transactions=stats["transactions"],
    )


@app.post(
    "/transaction",
    response_model=TransactionResponse,
    responses={
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
    tags=["transactions"],
)
def post_transaction(payload: TransactionRequest) -> TransactionResponse:
    """Record a transaction.

    * Validated by ``TransactionRequest`` before this runs.
    * Idempotent on ``transactionId`` — safe to retry.
    * Rate-limited per user to curb abuse.
    """
    result, _duplicate = store.record_transaction(
        userId=payload.userId,
        amount=payload.amount,
        tx_type=payload.type,
        transactionId=payload.transactionId,
    )
    return TransactionResponse(**result)


@app.get(
    "/summary/{userId}",
    response_model=UserSummary,
    responses={404: {"model": ErrorResponse}},
    tags=["transactions"],
)
def get_summary(
    userId: str = Path(..., description="The user to summarise."),
) -> UserSummary:
    """Return aggregate stats and recent transactions for a single user."""
    return UserSummary(**store.get_summary(userId))


@app.get("/ranking", response_model=RankingResponse, tags=["ranking"])
def get_ranking() -> RankingResponse:
    """Return the full leaderboard, best first, with per-factor breakdowns."""
    now = datetime.now(timezone.utc).timestamp()
    users = store.snapshot_users()
    ranking = compute_ranking(users, now)
    return RankingResponse(
        generatedAt=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        userCount=len(users),
        weights={
            "volume": config.RANK_WEIGHT_VOLUME,
            "frequency": config.RANK_WEIGHT_FREQUENCY,
            "recency": config.RANK_WEIGHT_RECENCY,
        },
        ranking=[
            {
                "rank": e["rank"],
                "userId": e["userId"],
                "score": e["score"],
                "totalValue": e["totalValue"],
                "transactionCount": e["transactionCount"],
                "lastActivity": datetime.fromtimestamp(
                    e["last_activity"], tz=timezone.utc
                ).isoformat(),
                "breakdown": e["breakdown"],
            }
            for e in ranking
        ],
    )


# ==========================================================================
# Demo-only helpers (guarded by ENABLE_DEMO_ENDPOINTS).
# ==========================================================================
if config.ENABLE_DEMO_ENDPOINTS:

    @app.post("/demo/reset", tags=["demo"])
    def demo_reset() -> dict:
        store.reset()
        return {"status": "ok", "message": "All in-memory data cleared."}

    @app.post("/demo/seed", tags=["demo"])
    def demo_seed() -> dict:
        """Populate a spread of users/transactions for a quick demo."""
        store.reset()
        sample_users = ["alice", "bob", "carol", "dave", "erin"]
        types = list(config.ALLOWED_TRANSACTION_TYPES)
        rng = random.Random(42)  # deterministic seed data
        created = 0
        for uid in sample_users:
            for _ in range(rng.randint(2, 8)):
                store.record_transaction(
                    userId=uid,
                    amount=round(rng.uniform(5, 500), 2),
                    tx_type=rng.choice(types),
                    transactionId=str(uuid.uuid4()),
                )
                created += 1
        return {
            "status": "ok",
            "message": f"Seeded {len(sample_users)} users / {created} transactions.",
            "users": sample_users,
        }
