# Transaction & Ranking Service

A small backend service (Python / FastAPI) plus a live, zero-build frontend
that together demonstrate solid backend fundamentals: **API design, request
validation, idempotency (duplicate-request prevention), concurrency-safe and
consistent data updates, a fair multi-factor ranking, and basic
abuse/manipulation prevention.**

> **Submission links**
> - **GitHub repo:** `https://github.com/Samyak-Waghmare/transaction-ranking-service`
> - **Live frontend:** `https://thunderous-monstera-1bcbaf.netlify.app/`
> - **Live backend (API docs):** `https://transaction-ranking-api-s8hj.onrender.com/docs`
> - **Demo video (3–5 min):** `(Add video link here after recording)`

---

## Table of contents
1. [What it does](#what-it-does)
2. [Tech stack](#tech-stack)
3. [Project structure](#project-structure)
4. [How to run the project](#how-to-run-the-project)
5. [How each API works](#how-each-api-works)
6. [How ranking is calculated](#how-ranking-is-calculated)
7. [How duplicate requests are prevented](#how-duplicate-requests-are-prevented)
8. [Concurrency & data consistency](#concurrency--data-consistency)
9. [Validation & abuse prevention](#validation--abuse-prevention)
10. [Data model (in-memory store)](#data-model-in-memory-store)
11. [Assumptions & mock data](#assumptions--mock-data)
12. [Trade-offs & limitations](#trade-offs--limitations)
13. [Configuration](#configuration)
14. [Testing](#testing)
15. [Deployment](#deployment)

---

## What it does

The domain is a **points/activity leaderboard**. Users generate value through
*transactions* (a purchase, a referral, a completed task, etc.). The service
records those transactions, lets you look up a per-user summary, and produces a
ranking that is **fair** — it rewards genuine, sustained activity and resists
gaming.

Three core APIs:

| Method & path           | Purpose                                            |
| ----------------------- | -------------------------------------------------- |
| `POST /transaction`     | Record a transaction (validated, idempotent, rate-limited). |
| `GET /summary/:userId`  | Aggregate stats + recent transactions for a user.  |
| `GET /ranking`          | The leaderboard, best-first, with score breakdowns.|

Plus helpers: `GET /health`, `GET /` (info), and demo-only `POST /demo/seed` /
`POST /demo/reset`. Interactive docs are auto-generated at `/docs` (Swagger UI)
and `/redoc`.

## Tech stack

- **Backend:** Python 3.13, [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn.
  Pydantic v2 gives declarative, automatic request validation.
- **Storage:** thread-safe **in-memory** store (explained
  [below](#data-model-in-memory-store)). No external services required to run.
- **Frontend:** plain HTML/CSS/JavaScript — **no build step**, so it deploys to
  any static host and is trivial to review.
- **Tests:** `pytest` (26 tests covering validation, idempotency, ranking,
  rate-limiting and real-thread concurrency).

## Project structure

```
.
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py      # all tunable limits & ranking weights (env-overridable)
│   │   ├── models.py      # Pydantic request/response schemas (validation lives here)
│   │   ├── store.py       # thread-safe in-memory store: idempotency, rate limit, aggregates
│   │   ├── ranking.py     # multi-factor scoring (pure, deterministic)
│   │   ├── errors.py      # domain exceptions
│   │   └── main.py        # FastAPI app: routes, CORS, error handlers
│   ├── tests/test_api.py  # full test suite
│   ├── conftest.py        # test path + per-test store reset
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   ├── Dockerfile
│   ├── Procfile
│   └── runtime.txt
├── frontend/
│   ├── index.html
│   ├── styles.css
│   ├── app.js             # fetches the API; configurable backend URL
│   └── config.js          # optional deploy-time default backend URL
├── render.yaml            # one-click Render blueprint (backend + frontend)
├── netlify.toml           # Netlify static-frontend config
└── README.md
```

## How to run the project

### Prerequisites
- Python 3.11+ (developed on 3.13). On Windows the `py` launcher is used below;
  on macOS/Linux use `python3`.

> **Windows note:** `cmd.exe` does **not** treat `#` as a comment. Use the
> dedicated *Windows (cmd.exe)* blocks below and copy them as-is — they contain
> no inline comments, so nothing breaks when pasted.

### 1) Backend

**Windows (cmd.exe)** — run each line, then visit `http://127.0.0.1:8000/docs`:

```bat
cd backend
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**macOS / Linux:**

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000        # docs at http://127.0.0.1:8000/docs
```

The API is now at `http://127.0.0.1:8000` and interactive docs at
`http://127.0.0.1:8000/docs`. The `uvicorn` command keeps the terminal busy —
leave it running and open a **new** terminal for the frontend (`Ctrl+C` stops it).

### 2) Frontend

The frontend is static. Open a **second** terminal (the `frontend` folder is at
the project root, *not* inside `backend`).

**Windows (cmd.exe)** — then visit `http://127.0.0.1:5500`:

```bat
cd /d "D:\New folder\Samyak\Projects\assignment backend\frontend"
py -3 -m http.server 5500
```

(From inside `backend\` you can instead use `cd ..\frontend`.)

**macOS / Linux:**

```bash
cd frontend
python3 -m http.server 5500       # then visit http://127.0.0.1:5500
```

In the page, paste the backend URL (`http://127.0.0.1:8000`) into **“Backend API
base URL”** and click **Connect**. The URL is remembered in `localStorage`.
Click **Seed sample data** to populate the leaderboard instantly.

### 3) Tests

**Windows (cmd.exe):**

```bat
cd backend
.venv\Scripts\activate
pip install -r requirements-dev.txt
pytest -q
```

**macOS / Linux:**

```bash
cd backend && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

## How each API works

All error responses share one shape:

```json
{ "error": { "code": "validation_error", "message": "…", "details": … } }
```

### `POST /transaction`

Records one transaction.

**Request body**

| Field           | Type    | Required | Rules                                                                 |
| --------------- | ------- | -------- | --------------------------------------------------------------------- |
| `userId`        | string  | yes      | 1–64 chars, `[A-Za-z0-9_.-]`. Trimmed.                                 |
| `amount`        | number  | yes      | Finite, `0.01 ≤ amount ≤ 1,000,000`. Rounded to 2 dp. NaN/Inf rejected.|
| `type`          | string  | no       | One of `purchase, referral, bonus, review, task, generic` (default `generic`). |
| `transactionId` | string  | no       | 1–128 chars. **Idempotency key** — supply a stable id to make retries safe. |

Unknown fields are rejected (`extra="forbid"`).

**Example**

```bash
curl -X POST http://127.0.0.1:8000/transaction \
  -H "Content-Type: application/json" \
  -d '{"userId":"alice","amount":125.50,"type":"purchase","transactionId":"abc-123"}'
```

**Response `200`**

```json
{
  "status": "created",
  "duplicate": false,
  "transactionId": "abc-123",
  "userId": "alice",
  "amount": 125.5,
  "type": "purchase",
  "processedAt": "2026-06-23T17:36:17.306495+00:00",
  "user": {
    "userId": "alice",
    "totalValue": 125.5,
    "transactionCount": 1,
    "averageTransaction": 125.5,
    "firstSeen": "…",
    "lastActivity": "…",
    "transactions": [ { "transactionId": "abc-123", "amount": 125.5, "type": "purchase", "timestamp": "…" } ]
  },
  "message": "Transaction recorded successfully."
}
```

Sending the **same `transactionId` again** returns `200` with
`"status": "duplicate"`, `"duplicate": true`, and the original totals — it is
**not** counted twice.

**Status codes:** `200` ok / duplicate · `422` validation error · `429` rate
limit exceeded (includes a `Retry-After` header).

### `GET /summary/:userId`

Aggregate view for one user. `404` (`user_not_found`) if the user has no
transactions.

```bash
curl http://127.0.0.1:8000/summary/alice
```

Returns `totalValue`, `transactionCount`, `averageTransaction`, `firstSeen`,
`lastActivity`, and the most recent transactions (newest first, capped at 50).

### `GET /ranking`

The full leaderboard, best-first.

```bash
curl http://127.0.0.1:8000/ranking
```

```json
{
  "generatedAt": "…",
  "userCount": 2,
  "weights": { "volume": 0.5, "frequency": 0.2, "recency": 0.3 },
  "ranking": [
    {
      "rank": 1,
      "userId": "bob",
      "score": 0.987157,
      "totalValue": 1665.96,
      "transactionCount": 6,
      "lastActivity": "…",
      "breakdown": { "volume": 0.5, "frequency": 0.2, "recency": 0.287 }
    }
  ]
}
```

`breakdown.volume + breakdown.frequency + breakdown.recency == score` exactly,
so you can see *why* a user ranks where they do.

## How ranking is calculated

The score blends **three independent factors** so no single behaviour can run
away with the board. (Weights are configurable and must sum to `1.0`.)

| Factor      | Signal                              | Default weight | Anti-gaming property                                  |
| ----------- | ----------------------------------- | -------------- | ----------------------------------------------------- |
| **Volume**  | total value generated               | `0.50`         | `log10(1 + total)` → diminishing returns              |
| **Frequency** | number of distinct transactions    | `0.20`         | `log10(1 + count)` → diminishing returns + rate limit |
| **Recency** | how recently the user was active    | `0.30`         | half-life time decay → can’t “win once and coast”     |

**Per-user components**

```
volume_raw     = log10(1 + total_value)
frequency_raw  = log10(1 + transaction_count)
recency        = 0.5 ** (days_since_last_activity / HALF_LIFE_DAYS)   # in (0, 1]
```

`volume_raw` and `frequency_raw` are then **normalised against the strongest
user in the field** (divide by the current max), so all three components live
on a comparable `[0, 1]` scale. `recency` is already absolute in `[0, 1]`.

**Final score** (always in `[0, 1]`):

```
score =  W_VOLUME    * (volume_raw    / max_volume_raw)
       + W_FREQUENCY * (frequency_raw / max_frequency_raw)
       + W_RECENCY   * recency
```

**Why this is fair / hard to manipulate**
- **Diminishing returns (logarithm):** one whale transaction or a burst of spam
  transactions yields rapidly shrinking marginal score — you can’t buy the #1
  spot with a single huge `amount`, nor spam your way up cheaply.
- **Time decay:** inactivity steadily pushes a user down (score halves every
  `HALF_LIFE_DAYS`), so the board reflects *current* engagement.
- **Server-assigned timestamps:** clients cannot backdate/forward-date a
  transaction to fake recency — the server stamps the time.
- **Velocity limiting** (see below) caps the spam that could inflate frequency
  in the first place.
- **Deterministic tie-breaking:** `score → totalValue → most-recent → userId`,
  so the ranking is stable and reproducible.

There is a test (`test_one_huge_tx_does_not_dominate…`) asserting that a single
`1,000,000` transaction does **not** outrank a user who is frequently and
recently active — demonstrating the fairness intent.

## How duplicate requests are prevented

Duplicate processing is prevented with an **idempotency key**: the optional
client-supplied `transactionId`.

- The store keeps a map `transactionId -> StoredTransaction`.
- On `POST /transaction`, **before** anything else, the store checks that map
  while holding the lock. If the id is already present, it returns the
  **original** stored result with `duplicate: true` and does **not** touch the
  user’s totals — so a network retry / double-click / at-least-once delivery is
  processed **exactly once**.
- The duplicate check happens *before* the rate limiter, so retries are never
  penalised as if they were new traffic.
- If a client omits `transactionId`, the server generates a unique `srv-<uuid>`
  id. Those requests can’t be de-duplicated (there’s no stable key), which is
  the correct behaviour — documented so real clients know to send their own id.

This is verified both serially (`test_duplicate_transaction_id_processed_once`)
and **under concurrency** (`test_concurrent_same_id_processed_exactly_once`:
100 threads fire the same id; exactly one is `created`, 99 are `duplicate`,
final count is `1`).

## Concurrency & data consistency

FastAPI runs the synchronous route handlers in a worker **thread pool**, so two
requests genuinely can mutate shared state at the same time. The store guards
every read-then-write with a single `threading.RLock`:

- **Atomic updates:** creating/looking-up the user, folding the amount into the
  aggregates (`total_value`, `transaction_count`, `last_activity`), indexing the
  transaction, and updating the rate-limit window all happen inside **one**
  critical section. No lost updates, no half-applied transactions.
- **Consistent aggregates:** because the aggregate counters are updated in the
  same locked block that appends the transaction, the summary can never
  disagree with the underlying transaction list.
- **Consistent reads for ranking:** `/ranking` takes a point-in-time *snapshot*
  of users inside the lock, then computes scores on that stable copy — so the
  leaderboard is internally consistent even while new transactions stream in.

`test_concurrent_unique_transactions_no_lost_updates` fires 300 concurrent
transactions for one user from real threads and asserts the final count and
total are exactly right (no lost updates).

## Validation & abuse prevention

- **Schema validation (422):** Pydantic validates/coerces every field before
  business logic runs — required fields, numeric bounds, `userId` charset/length,
  allowed `type`, and rejection of unknown fields and `NaN`/`Infinity`.
- **Rate limiting / velocity (429):** at most `RATE_LIMIT_MAX_TX` *new*
  transactions per `RATE_LIMIT_WINDOW_SECONDS` per user (sliding window). The
  response includes a `Retry-After` header. Idempotent retries don’t count.
- **Amount caps & rounding:** amounts are bounded and rounded to 2 dp, blocking
  float-dust spam (e.g. `0.0000001`) and absurd values.
- **Server-side timestamps:** prevents recency manipulation.
- **Ranking dampening:** logarithmic diminishing returns (see ranking section).
- **No stack-trace leaks:** a catch-all handler returns a generic `500` body.

## Data model (in-memory store)

State lives in process memory (allowed by the brief, and it lets the
*interesting* concerns — atomicity, idempotency, concurrency — be shown
directly rather than delegated to a DB engine). Three structures, all behind one
lock:

| Structure        | Type                                  | Role                                            |
| ---------------- | ------------------------------------- | ----------------------------------------------- |
| `_users`         | `dict[userId -> UserAccount]`         | per-user aggregates + bounded recent-tx history |
| `_transactions`  | `dict[transactionId -> StoredTransaction]` | idempotency index + audit of every processed tx |
| `_rate_windows`  | `dict[userId -> deque[timestamp]]`    | sliding window for velocity limiting            |

`UserAccount` holds `total_value`, `transaction_count`, `first_seen`,
`last_activity`, and a capped `deque` of recent transactions (aggregates always
reflect **all** transactions; only the returned detail list is trimmed).

The store is intentionally isolated behind a small method API
(`record_transaction`, `get_summary`, `snapshot_users`, …) so it could be
swapped for **Redis or PostgreSQL** with no change to the routes or ranking — in
that case the lock becomes a DB transaction / `SELECT … FOR UPDATE` and the
idempotency map becomes a unique constraint on `transaction_id`.

## Assumptions & mock data

- **Domain:** a transaction is a **positive, value-generating** event. There are
  no debits/refunds; `amount` is always `> 0`. (Net balances/refunds would be a
  straightforward extension.)
- **Users are implicit:** there’s no separate “create user” step — a user is
  created on first transaction. `GET /summary` for an unknown user is `404`.
- **Idempotency scope:** de-duplication is global and for the lifetime of the
  process; ids are assumed reused only for genuine retries of the *same* logical
  transaction.
- **Ranking is relative:** volume/frequency are normalised against the current
  field, so scores shift as the population changes — appropriate for a
  leaderboard.
- **State is ephemeral:** because storage is in-memory, restarting the backend
  clears all data. The **`POST /demo/seed`** endpoint deterministically
  populates 5 users / ~28 transactions so the live demo and reviewers always
  have data to look at; **`POST /demo/reset`** clears it. These are guarded by
  `ENABLE_DEMO_ENDPOINTS` (on by default).
- **CORS** defaults to `*` so the static frontend (different origin) can call
  the API; lock it down via `CORS_ALLOW_ORIGINS` in production.

## Trade-offs & limitations

- **In-memory ⇒ not durable and single-instance.** Data is lost on restart and
  not shared across replicas. For production: move the store to Redis/Postgres
  (the code is structured for this) so it survives restarts and scales
  horizontally. The per-process lock would become DB-level concurrency control.
- **Ranking is computed on demand** by scanning all users — simple and exact,
  great up to tens of thousands of users. At larger scale you’d precompute and
  cache scores or maintain a sorted structure.
- **Rate limiting is per-process & per-user** (in-memory window). Behind a load
  balancer you’d use a shared store (e.g. Redis) for global limits.
- **No authentication.** The brief doesn’t require it; in production each
  request would carry an authenticated identity rather than trusting `userId`.

## Configuration

All tunables live in `backend/app/config.py` and can be overridden by
environment variables:

| Variable                    | Default     | Meaning                                  |
| --------------------------- | ----------- | ---------------------------------------- |
| `MIN_TRANSACTION_AMOUNT`    | `0.01`      | Smallest accepted amount                 |
| `MAX_TRANSACTION_AMOUNT`    | `1000000`   | Largest accepted amount                  |
| `RATE_LIMIT_MAX_TX`         | `20`        | Max new tx per window per user           |
| `RATE_LIMIT_WINDOW_SECONDS` | `10`        | Rate-limit window length                 |
| `RANK_WEIGHT_VOLUME`        | `0.50`      | Volume weight (weights must sum to 1)    |
| `RANK_WEIGHT_FREQUENCY`     | `0.20`      | Frequency weight                         |
| `RANK_WEIGHT_RECENCY`       | `0.30`      | Recency weight                           |
| `RECENCY_HALF_LIFE_DAYS`    | `7`         | Days for the recency factor to halve     |
| `CORS_ALLOW_ORIGINS`        | `*`         | Comma-separated allowed origins          |
| `ENABLE_DEMO_ENDPOINTS`     | `1`         | Set `0` to disable `/demo/*`             |

Invalid config (e.g. weights not summing to 1) fails fast at startup.

## Testing

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q          # 26 tests
```

Coverage highlights: field validation & `NaN`/`Inf` rejection, amount rounding,
idempotent duplicates (serial **and** concurrent), summary `404`, ranking order
+ bounded scores + breakdown-sums-to-score + “whale doesn’t dominate”, rate
limiting (`429` + `Retry-After`), and **300-thread** no-lost-updates concurrency.

## Deployment

The frontend reads the backend URL from the UI (saved to `localStorage`), so the
two can be deployed independently and wired together at runtime — no rebuild.

### Backend
- **Render (recommended, blueprint included):** the root `render.yaml` defines
  both services. In Render → **New + → Blueprint**, select the repo, **Apply**.
  Or create a single **Web Service** with root `backend/`, build
  `pip install -r requirements.txt`, start
  `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/health`.
- **Railway / Heroku-style:** uses `backend/Procfile`.
- **Docker (Railway / Fly.io / Cloud Run):** `backend/Dockerfile`.

### Frontend (static — pick one)
- **Netlify:** repo includes `netlify.toml` (publishes `frontend/`). Or drag the
  `frontend/` folder into Netlify Drop.
- **Vercel:** import the repo and set the project root/output to `frontend`.
- **GitHub Pages:** serve the `frontend/` folder.

After deploy, open the frontend, paste the live backend URL into **“Backend API
base URL”**, click **Connect**, then **Seed sample data**. (Optionally set
`window.__API_BASE__` in `frontend/config.js` before deploying to skip the
manual step.)

---

Built to be **correct, consistent, fair, and easy to review.**
