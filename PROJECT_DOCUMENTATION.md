# Aurelia — Complete Project Documentation

## 1. Project overview

**Aurelia** is a multi-user personal-finance platform. It provides a private workspace where each signed-in user can record and import transactions, define category budgets, create savings goals, view financial summaries, and request analysis from an AI-assisted financial workflow.

The platform is intentionally designed so that **financial calculations are deterministic and traceable**. AI can categorize transactions and explain verified findings, but it does not replace calculations, fabricate transactions or balances, or perform financial actions.

The current application does **not** use Streamlit for the new web experience. The active browser application is React/Vite, with an Express API, MongoDB persistence, and a Python FastAPI/LangGraph analysis service. The original Python `core/` package remains in the repository as the reusable deterministic finance engine.

---

## 2. What users can do

After authenticating with Google through Firebase, a user has an independent financial workspace with the following capabilities.

### Authentication and account workspace

- Sign in with a **Google account through Firebase Authentication**.
- Synchronize the verified Firebase user with the application database.
- Log out from the application.
- Access protected pages only after authentication.
- Keep financial data isolated by the verified Firebase UID.

### Dashboard

The dashboard shows calculated information from the user’s stored transactions:

- Configured initial balance.
- Total recorded income.
- Total recorded expenses.
- Savings (`income - expenses`).
- Estimated balance (`initial balance + income - expenses`).
- Recent transactions.
- Latest available financial-health data.
- Spending and cash-flow visualizations supplied by dashboard APIs.

An **estimated balance is not represented as a verified bank balance**.

### Transactions

Users can:

- Add income and expense transactions manually.
- Provide merchant, description, date, category, payment information, and amount.
- Search, filter, and paginate transactions.
- Edit transactions.
- Delete individual transactions.
- Delete all transactions in their own workspace.
- Import supported statement-like tabular files, including CSV and common Excel/text MIME types.
- Review and confirm, edit, or reject AI category suggestions.
- Trigger bulk categorization for uncategorized expense records.

The import route includes practical parsing and data-quality support:

- In-memory upload handling with a 10 MB file limit.
- CSV delimiter detection for comma, semicolon, tab, and pipe-separated files.
- Header discovery that can skip statement preambles.
- Common aliases for date, amount, debit, credit, merchant, narration, reference, type, and category columns.
- Date and amount cleanup.
- Income/expense inference from debit/credit or type columns.
- Duplicate detection based on date, merchant, amount, and direction.
- A per-upload limit of 5,000 parsed transaction rows.

### Budgets

Users can create monthly category budgets with a category, a monthly limit, and a `YYYY-MM` month.

Implemented budget actions include:

- List budgets.
- Create a budget.
- Upsert a budget by category and month.
- Edit a budget’s monthly limit inline.
- Delete a budget after confirmation.
- Request budget recommendations based on the user’s own transaction history.
- Explicitly accept a recommendation before it is applied.

No recommendation modifies a budget automatically.

### Goals

Users can create, view, update, and delete financial goals. A goal stores its name, description, target amount, current amount, target date, and state.

The application can request a goal cash-flow impact analysis. It calculates the remaining target amount and an estimated monthly saving requirement, then asks the AI service to explain the effect using the user’s own transaction history. It does not initiate transfers, savings plans, purchases, or other external actions.

### Financial analysis and reporting screens

The web interface includes pages for:

- Financial analysis.
- Financial health.
- Cash-flow forecast.
- AI advisor questions.
- Reports.

These screens call authenticated API routes that run the financial workflow using only records fetched from the current user’s workspace. Workflow results can be stored and later listed through the workflow-history APIs.

---

## 3. Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│ React + Vite + TypeScript frontend (frontend/)               │
│ Google sign-in via Firebase client SDK                        │
└──────────────────────────────┬───────────────────────────────┘
                               │ Firebase ID token (Bearer)
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ Express + TypeScript API (backend/)                           │
│ Firebase Admin token verification · Zod validation · auditing │
└───────────────┬─────────────────────────────────┬────────────┘
                │                                 │
                ▼                                 ▼
┌──────────────────────────────┐   ┌───────────────────────────┐
│ MongoDB / Mongoose           │   │ Python FastAPI service     │
│ User-owned persistent data   │   │ LangGraph finance workflow │
└──────────────────────────────┘   └─────────────┬─────────────┘
                                                   │
                                                   ▼
                                   ┌───────────────────────────┐
                                   │ Deterministic core/ engine │
                                   │ Optional Groq interpretation│
                                   └───────────────────────────┘
```

### Main directories

| Directory | Purpose |
| --- | --- |
| `frontend/` | React/Vite/TypeScript web application, routes, Firebase client login, Axios API client, Tailwind styling, charts. |
| `backend/` | Express API, Firebase Admin verification, Mongoose models, validation, auditing, imports, dashboard data, and AI proxy routes. |
| `ai-service/` | Internal FastAPI application, LangGraph workflow, Pydantic contracts, Groq integration, prompts, and FastMCP server. |
| `core/` | Reusable deterministic finance calculations, validation, categorization, governance, agents, reports, alerts, tools, and storage concepts. |
| `mcp_server/` | Existing legacy MCP-related source retained with the original project. |
| `shared/api-contracts/` | Documentation of the public/API/internal-service trust boundary. |
| `tests/` | Existing deterministic-engine test suite. |

---

## 4. Frontend implementation

The frontend uses **React 18**, **Vite**, **TypeScript**, **React Router**, **Axios**, **Tailwind CSS**, and **Recharts**.

### Routes

| Route | Page |
| --- | --- |
| `/login` | Google sign-in screen. |
| `/dashboard` | Financial overview and recent activity. |
| `/transactions` | Transaction management and statement import workflow. |
| `/budgets` | Monthly budgets, inline edit/delete, and recommendations. |
| `/goals` | Financial goals and goal impact analysis. |
| `/intelligence` | Financial analysis output. |
| `/health` | Financial health details. |
| `/forecast` | Cash-flow forecast. |
| `/advisor` | AI advisor interaction. |
| `/reports` | Report-oriented financial results. |

All routes other than `/login` are protected by the authentication context.

### Firebase client behavior

The Firebase client configuration is read from public Vite environment variables:

- `VITE_FIREBASE_API_KEY`
- `VITE_FIREBASE_AUTH_DOMAIN`
- `VITE_FIREBASE_PROJECT_ID`
- `VITE_FIREBASE_APP_ID`

The frontend avoids crashing when those values are missing. Instead, it shows a configuration error and disables sign-in. When a Firebase user is active, Axios obtains a Firebase ID token and sends it as:

```http
Authorization: Bearer <Firebase ID token>
```

The default API base URL is `http://127.0.0.1:4000/api`, unless `VITE_API_URL` is configured.

---

## 5. Backend API and persistence

The backend uses **Express**, **TypeScript**, **Mongoose**, **Zod**, **Firebase Admin**, **Helmet**, **CORS**, **express-rate-limit**, **Multer**, and **Axios**.

### Data model

MongoDB collections currently defined by the application are:

| Collection/model | Stored responsibility |
| --- | --- |
| `User` | Firebase UID, display name, email, initial balance, preferences, and account status. |
| `Transaction` | User-owned income/expense records, source, category metadata, AI suggestion metadata, and validation status. |
| `Budget` | Category monthly limit and month for a user. |
| `Goal` | User savings or purchase goal. |
| `Subscription` | Recurring-payment data model for finance intelligence. |
| `FinancialReport` | Model reserved for persisted report results. |
| `HealthHistory` | Historical financial-health results. |
| `AuditLog` | Security and activity audit entries. |
| `AgentWorkflow` | Stored AI workflow identifiers, status, and result payloads. |

User-owned models use a `userId` string containing the verified Firebase UID. Useful indexes include transaction date ordering, per-user transaction ID uniqueness, per-user budget category/month uniqueness, and workflow uniqueness.

### Core public API groups

The API is mounted below `/api`, except for `GET /health`.

| Area | Implemented operations |
| --- | --- |
| Health | `GET /health` returns service health. |
| Auth | `POST /auth/session`, `GET /auth/me`, `POST /auth/logout`. All require a verified Firebase token. |
| User balance | `GET` and `POST /user/initial-balance`. |
| Transactions | List, create, update, delete, delete-all, and filtered/paginated retrieval. |
| Budgets | List, create, update, delete, and `POST /budgets/upsert`. |
| Goals | List, create, update, delete, and `POST /goals/:id/impact`. |
| Dashboard | Summary, cash flow, spending by category, and financial-health history. |
| Import | Authenticated transaction import, category confirmation, and bulk categorization. |
| AI | Analyze, forecast, report, chat, budget recommendations, service status, and workflow history. |

### Validation and request controls

- Zod validates transaction, budget, and goal write bodies.
- JSON request bodies are limited to 1 MB.
- Rate limiting is enabled for the API.
- Uploads are kept in memory and restricted by size.
- Helmet sets HTTP security headers.
- CORS allows configured local development origins, including `http://localhost:5173` and `http://127.0.0.1:5173`.

---

## 6. Authentication, authorization, and data isolation

### Firebase authentication flow

1. The user selects **Continue with Google** in the browser.
2. Firebase performs the Google popup sign-in.
3. The frontend gets an Firebase ID token from the signed-in user.
4. The frontend calls `/api/auth/session` with that token.
5. The Express middleware verifies the token through Firebase Admin SDK.
6. The backend takes the `uid`, email, and display name from the verified token and creates or updates the local `User` record.
7. Requests use that same verified UID to find the user’s records.

The application does not trust a user ID submitted by the browser or by an AI prompt.

### Owner-scoped data access

Routes that access financial records build their query from the authenticated user’s Firebase UID. Updates and deletes use both the requested record ID and `userId`, so one user cannot update or delete another user’s record merely by guessing an identifier.

### AI trust boundary

The browser never calls the Python AI service directly.

1. Express verifies the Firebase token.
2. Express fetches only the current user’s transactions and budgets from MongoDB.
3. Express signs a short-lived internal JWT with `INTERNAL_SERVICE_SECRET`.
4. The Python service requires the JWT, validates issuer `aurelia-api` and audience `aurelia-ai`, and receives only server-provided records.

This prevents a browser client from supplying a different user ID to the AI service.

### Sensitive configuration

Do not commit `.env` or the Firebase Admin service account JSON file. The project ignores them through `.gitignore`.

Server configuration includes:

- MongoDB connection string.
- Internal service signing secret.
- Firebase Admin service-account file path.
- API and AI service URLs.
- Firebase web configuration values for the frontend.
- Optional Groq configuration.

Secret values are deliberately not included in this document.

---

## 7. Active LangGraph financial workflow

The current internal analysis service is a FastAPI application. Its protected endpoints include:

- `POST /internal/ai/categorize`
- `POST /internal/ai/analyze`
- `POST /internal/ai/forecast`
- `POST /internal/ai/report`
- `POST /internal/ai/chat`
- `POST /internal/ai/budget-recommendations`
- `GET /internal/ai/status`
- `GET /health`

### Workflow order

```text
Normalize transactions
        ↓
LLM categorization (optional, non-blocking)
        ↓
Deterministic financial analysis
        ↓
LLM interpretation of verified metrics (optional, non-blocking)
        ↓
Advisor response for a user question (when supplied)
```

Each run receives a generated workflow ID such as `WF-...`, returns a completion status and results, and can be persisted by the Express API in `AgentWorkflow`.

### Active workflow nodes / agents

| Node | What it does | Type |
| --- | --- | --- |
| **Transaction processing** | Validates and normalizes incoming transaction records into the core transaction model. Invalid records are returned as rejected for analysis. | Deterministic |
| **LLM categorization** | Looks at uncategorized (`Other`) transactions and asks the configured Groq model for category suggestions. Suggestions at confidence `>= 0.5` are applied to workflow copies only; failure does not stop analysis. | Optional AI augmentation |
| **Financial analysis** | Runs budgets, anomaly detection, subscriptions, spending intelligence, health score, forecast, recommendations, and alerts using the deterministic `core/` functions. | Deterministic |
| **LLM analysis interpretation** | Sends already calculated/verified values to an LLM to produce narrative observations. It does not recalculate values or replace metrics. | Optional AI augmentation |
| **Advisor** | Answers a user question using verified financial context. If the LLM is unavailable or returns no result, it generates a limited deterministic, evidence-based response. | AI with deterministic fallback |

### What deterministic analysis produces

The workflow computes and returns data for:

- Valid and rejected transactions.
- Category and month-based spending intelligence.
- Budget utilization and over-budget indicators.
- Unusual transaction patterns/anomalies.
- Suspected recurring subscriptions.
- Financial-health score and its components.
- Cash-flow forecast and projected deficit indication.
- Actionable recommendations.
- Alerts derived from recommendations and anomalies.
- Per-step status and non-blocking error details.

### Financial health formula

The retained finance engine documents a transparent weighted score:

```text
score = 0.40 × savings + 0.30 × budget adherence + 0.30 × emergency coverage
```

If an input is unavailable, the result is identified as incomplete/provisional rather than silently inventing an input value.

### Cash-flow and recommendation safeguards

- Forecasts are estimates based on records and recognized recurring patterns.
- Forecasts are not bank balances or promises of future cash flow.
- Recommendations are informational and require the user’s approval before any application state changes.
- The system does not automate transfers, investment trades, payments, subscription cancellation, or other external financial actions.

---

## 8. Budget recommendation service

Budget recommendations are produced from valid, user-owned transactions and existing budgets.

The service:

1. Aggregates expense spending by category.
2. Calculates the number of months represented and average income where present.
3. Provides those verified aggregates to the optional Groq model.
4. Marks every returned recommendation as requiring user approval.

If the Groq client is not configured or does not return usable recommendations, the service uses a transparent rule-based fallback:

```text
recommended budget = historical category average × 0.95
```

The fallback labels its limitation rather than claiming an AI-generated result.

---

## 9. Retained deterministic agent framework (`core/agents.py`)

The repository also retains a more granular asynchronous agent framework. It is valuable reusable deterministic domain logic and defines explicit dependencies and failure handling. These agents do not fabricate missing upstream results; when a dependency has not completed, the framework records a skip/failure state.

| Agent | Responsibility |
| --- | --- |
| **IngestionAgent** | Reads a staged statement or existing validated records and records ingestion status. |
| **ValidationAgent** | Validates staged rows, quarantines rejected records, and loads usable transactions. |
| **CategorizationAgent** | Categorizes transactions using deterministic rules and optional AI support. |
| **SubscriptionAgent** | Detects recurring payments and flags entries that need user verification. |
| **AnomalyAgent** | Finds unusual transaction patterns and highlights high/critical signals. |
| **SpendingAgent** | Produces monthly/category spending intelligence and top-spend context. |
| **BudgetAgent** | Evaluates budget utilization and categories over their configured limits. |
| **FinancialScoringAgent** | Calculates financial-health score and identifies unavailable components. |
| **CashFlowAgent** | Produces a cash-flow forecast and flags a projected deficit. |
| **FinancialAdvisorAgent** | Builds evidence-based recommendations from scoring, forecast, anomaly, and subscription results. |
| **NotificationAgent** | Applies alert/notification handling to actionable findings. |

The active LangGraph workflow reuses the underlying deterministic financial functions from `core/`; it is a simpler service-oriented orchestration path for the new web application.

---

## 10. FastMCP tool service

`ai-service/mcp/server.py` provides a standalone **FastMCP** server running on loopback by default. It exposes deterministic tools that operate on records supplied by the application:

| MCP tool | Purpose |
| --- | --- |
| `detect_anomalies` | Finds unusual patterns in supplied authorized transactions. |
| `detect_subscriptions` | Finds potential recurring payments. |
| `calculate_financial_health` | Calculates transparent health data from supplied records and budgets. |
| `forecast_cashflow` | Produces a cash-flow forecast from supplied records. |

The MCP service does not expose arbitrary database access, arbitrary filesystem access, or code execution. Its inputs are intended to be application-scoped records.

---

## 11. Local development setup

### Prerequisites

- Node.js and npm.
- Python.
- A MongoDB deployment/connection string.
- Firebase project with Google provider enabled.
- Firebase web-app configuration.
- Firebase Admin SDK service-account JSON stored outside version control.
- Optional Groq API configuration for LLM features.

### Environment configuration

1. Copy `.env.example` to `.env`.
2. Configure MongoDB, internal service secrets, Firebase service-account path, and Firebase public web values.
3. Copy the applicable public Vite values to `frontend/.env` so Vite can inject them when it starts.
4. Do not put secret credentials in frontend variables.

### Install dependencies

```bash
npm install --prefix backend
npm install --prefix frontend
python -m pip install -r ai-service/requirements.txt
```

### Start services

Start the internal Python service from the project root:

```bash
PYTHONPATH="$(pwd):$(pwd)/ai-service" uvicorn main:app --app-dir ai-service --host 127.0.0.1 --port 8000
```

Start the Express API:

```bash
npm run dev --prefix backend
```

Start the frontend:

```bash
npm run dev --prefix frontend
```

Expected local URLs:

| Service | URL |
| --- | --- |
| Frontend | `http://localhost:5173` or `http://127.0.0.1:5173` |
| Express API | `http://127.0.0.1:4000` |
| API health | `http://127.0.0.1:4000/health` |
| AI service | `http://127.0.0.1:8000` |
| MCP server, when started | `http://127.0.0.1:8001` |

### Firebase Console setup required for Google sign-in

In Firebase Console:

1. Enable the **Google** sign-in provider.
2. Add development authorized domains such as `localhost` and `127.0.0.1` as applicable.
3. Confirm that the Firebase web configuration matches the frontend environment values.
4. Ensure the Admin service account belongs to the same Firebase project.

---

## 12. Verification performed during migration

The repository has previously been checked with:

```bash
python -m pytest -q --tb=line -p no:cacheprovider
python -m compileall -q ai-service
npm run build --prefix backend
npm run build --prefix frontend
```

The existing Python deterministic-engine suite recorded **113 passed and 1 skipped** at the time of migration verification. The frontend and backend builds have also been run successfully during the migration work.

---

## 13. Current implementation boundaries

The following points describe the current state accurately and should be addressed before production use where relevant:

- MongoDB connectivity, Firebase configuration, and service-account configuration are required for a complete end-to-end login/data test.
- Google authentication depends on Firebase Console provider and authorized-domain configuration.
- The LLM-enhanced features require optional Groq configuration; deterministic finance functions can still run without it where a fallback exists.
- The workflow stores completed API-level workflow results in MongoDB, but durable LangGraph checkpoint/resume storage is not implemented.
- `FinancialReport`, `Subscription`, and health-history models exist; some are presently data models or analysis outputs rather than a complete independent CRUD/report-export product.
- The backend import implementation focuses on CSV-style parsing. Although several spreadsheet MIME types are accepted by upload filtering, a dedicated Excel workbook parser is not currently implemented in this route.
- Full production-grade multi-account integration testing, report-download formatting, and deployment hardening remain future work.
- Financial insights are educational and analytical only; they are not regulated financial advice, transaction execution, or bank-account verification.

---

## 14. Design principles

Aurelia follows these core principles:

1. **Identity comes from verified authentication**, never from a browser-supplied user ID.
2. **Every financial query is owner-scoped** to preserve multi-user isolation.
3. **Deterministic calculations are authoritative**; LLM output augments explanations and suggestions only.
4. **Missing data remains missing**; the system does not invent financial facts.
5. **User approval is required** for applied recommendations and modifications.
6. **No automatic external financial action** is performed.
7. **Internal services are protected** by a signed service-to-service boundary.
8. **Secrets, credentials, and raw private data are not intentionally logged or committed.**
