# Aurelia — Multi-User Personal Finance Platform

Aurelia is a multi-user personal finance platform with a React web application, an Express API, a MongoDB-backed user workspace, and a Python financial-analysis service. Financial calculations remain deterministic and traceable. AI interpretation is optional and must not invent balances, transactions, or performance metrics.

## Architecture

```text
React + Vite + TypeScript (frontend/)
       │ authenticated cookie session
       ▼
Express + TypeScript (backend/) ── MongoDB Atlas
       │ signed, short-lived internal JWT
       ▼
FastAPI + LangGraph + Python (ai-service/)
       │ deterministic financial functions + optional MCP tools
       ▼
FastMCP Python service (ai-service/mcp/)
```

The browser communicates only with the Express API. The API authenticates the user, scopes every MongoDB query to the verified user ID, fetches only that user's records, and signs a short-lived internal token for the Python service. The Python service validates that token and does not accept a user ID from a public request.

## Preserved financial engine

The Python `core/` package is retained as the authoritative deterministic engine for:

- Transaction normalization and validation
- Rule-first categorization
- Anomaly and duplicate-pattern detection
- Subscription detection
- Spending and merchant analysis
- Budget utilization
- Cash-flow forecasts
- Advisory evidence generation
- Alerts, approvals, and audit concepts

Financial health retains the transparent formula:

`score = 0.40 × savings + 0.30 × budget adherence + 0.30 × emergency coverage`

Unavailable inputs stay unavailable; they are not silently treated as zero. Forecasts are labelled estimates, and a calculated balance is never represented as a verified bank balance.

## Service directories

- `frontend/` — React, Vite, TypeScript, Tailwind CSS, React Router, Axios, and Recharts.
- `backend/` — Express API, Mongoose models, cookie-session JWT authentication, Zod validation, auditing, rate limiting, and secure headers.
- `ai-service/` — FastAPI internal API, LangGraph workflow, Pydantic contracts, and a standalone FastMCP server.
- `shared/api-contracts/` — documentation for the internal trust boundary.
- `core/` — existing deterministic financial logic reused by the AI service.
- `tests/` — existing engine tests.

## Data security

- Passwords are salted and hashed using bcrypt.
- Access and refresh tokens are HTTP-only cookies; production cookies require HTTPS.
- Passwords, tokens, secrets, and raw financial records are not written to application logs.
- Owner checks use the verified JWT subject in every API query; no endpoint trusts a frontend-provided user ID.
- Mongoose collections index `userId` and common query fields. Transaction ID uniqueness is per user.
- API requests are protected with Helmet, CORS credentials restrictions, JSON size limits, and rate limiting.
- The Python service is internal-only and rejects requests without a valid, audience-scoped service token.
- MCP tools accept only supplied application-scoped records; they provide no database, arbitrary filesystem, or code-execution access.
- No financial transaction, subscription change, investment, transfer, or external action is performed automatically.

## MongoDB collections

`User`, `Transaction`, `Budget`, `Goal`, `Subscription`, `FinancialReport`, `HealthHistory`, `AuditLog`, and `AgentWorkflow` are defined in `backend/src/models/index.ts`.

## Local setup

1. Copy `.env.example` to `.env` and provide a MongoDB Atlas connection string plus unique long secrets for `JWT_SECRET` and `INTERNAL_SERVICE_SECRET`.
2. Install JavaScript dependencies:

   ```bash
   npm install --prefix backend
   npm install --prefix frontend
   ```

3. Install the Python AI dependencies:

   ```bash
   python -m pip install -r ai-service/requirements.txt
   ```

4. Start the internal Python service from the project root:

   ```bash
   PYTHONPATH="$(pwd):$(pwd)/ai-service" uvicorn main:app --app-dir ai-service --host 127.0.0.1 --port 8000
   ```

5. Start the API:

   ```bash
   npm run dev --prefix backend
   ```

6. Start the web application:

   ```bash
   npm run dev --prefix frontend
   ```

For deployment, terminate TLS at the hosting layer, set `NODE_ENV=production`, use HTTPS-only cookies, configure exact CORS origins, and keep the Python internal endpoint inaccessible from the public internet.

## Verification run

The current migration was checked with:

```bash
python -m pytest -q --tb=line -p no:cacheprovider  # 113 passed, 1 skipped
python -m compileall -q ai-service
npm run build --prefix backend
npm run build --prefix frontend
```

The backend and full authenticated integration flow cannot be started until valid MongoDB Atlas and secret configuration values are supplied in `.env`. No sample financial records were inserted as part of the migration.

## Current migration scope

The service foundation, multi-user auth boundary, user-scoped CRUD for transactions/budgets/goals, summary and analysis APIs, React navigation/auth/dashboard screens, LangGraph workflow, and FastMCP service scaffold are implemented. Statement-import UI/API, password recovery delivery, report-download formatting, persistent LangGraph checkpoint storage, and full end-to-end multi-account integration tests remain to be completed before a production deployment.
