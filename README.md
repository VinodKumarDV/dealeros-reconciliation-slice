# DealerOS Reconciliation Slice

## 1. Running the Application

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- Node.js 18+

### PostgreSQL Setup (Manual Setup Only)

Install PostgreSQL 14 or newer and make sure its service is running. The following commands assume the standard PostgreSQL port (`5432`) and that `psql` is available in PowerShell. Run them from the repository root as the PostgreSQL administrator (usually `postgres`):

```powershell
psql -U postgres -h 127.0.0.1 -p 5432 -d postgres -c "CREATE DATABASE dealeros;"
psql -U postgres -h 127.0.0.1 -p 5432 -d dealeros -f db\init.sql
```

The first command creates the `dealeros` database. The SQL file creates the `dealeros_app` role and grants it the permissions needed by the application. If the database already exists, skip the first command. This setup is only needed when running PostgreSQL outside Docker Compose.

### Backend Setup

From the repository root, create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
```

Configure the PostgreSQL connection:

```powershell
$env:POSTGRES_HOST="127.0.0.1"
$env:POSTGRES_PORT="5432"
$env:POSTGRES_DB="dealeros"
$env:POSTGRES_USER="dealeros_app"
$env:POSTGRES_PASSWORD="dealeros_local"
```

If your PostgreSQL server uses a different port, use that port here. When using Docker Compose, skip this manual database setup; Compose starts and initializes PostgreSQL for you.

Start the backend:

```powershell
cd backend
python -c "from seed import ensure_schema_and_seed; ensure_schema_and_seed()"
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

The backend initializes the database and imports the supplied sample CSV data.

### Frontend Setup

Open another terminal from the repository root:

```powershell
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

Open the application at:

http://localhost:5173

### Demo Accounts

Two predefined accounts are available for testing tenant isolation.

| Username | Password | Organization |
|---|---|---|
| dealer-a | dealeros123 | ORG-A |
| dealer-b | dealeros123 | ORG-B |

Sign in with either account to view its organization's exceptions. The application keeps each user's organization associated with their server-side session.

### Running with Docker Compose

Docker Desktop (or Docker Engine with the Compose plugin) is required. From the repository root, build and start PostgreSQL, the backend, and the frontend:

```powershell
docker compose up --build
```

Wait until the services are running, then open http://localhost:5173. The backend is available at http://localhost:8000 and imports the sample CSVs during startup. Sign in with either demo account from the table above.

To confirm the services are running, use another terminal:

```powershell
docker compose ps
Invoke-RestMethod http://localhost:8000/health
```

Run the backend tests and frontend production build inside their containers:

```powershell
docker compose exec backend python -m pytest -q tests
docker compose exec frontend npm run build
```

Stop the services with `Ctrl+C`, or run `docker compose down` from another terminal. The PostgreSQL data volume is preserved. To delete the local database and start fresh, run `docker compose down -v`; this permanently removes the local Compose database data.

### Running Tests

From the backend directory:

```powershell
python -m pytest -q tests
```

## 2. API Overview

The FastAPI backend runs on port `8000`.

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/login` | Sign in with a demo account |
| GET | `/api/exceptions` | Retrieve exceptions for the signed-in organization |
| GET | `/api/filters` | Get available reason codes and locations |
| POST | `/api/ask` | Ask questions about the available exceptions |

The exceptions endpoint supports filtering by reason code and location.

The question endpoint accepts a JSON request:

```json
{
  "question": "How many total exceptions are there?"
}
```

It supports a limited set of questions, including exception counts, location-based counts, amount totals for mismatches, and record-level lookups.

Answers include the relevant record identifiers (`REC-*`) and System B entry identifiers (`ENT/*`) wherever applicable.

Questions about information that isn't available, such as future predictions, individual responsibility, or customer contact details, are refused rather than answered with assumptions.

## 3. What Is Built

The application covers the main reconciliation workflow from CSV import to the exceptions table.

1. **CSV Reconciliation:** Compares System A and System B records and identifies disagreements, assigning reason codes that are understandable to non-technical users.
2. **FastAPI Backend:** Handles login, session management, exception retrieval, filtering, and question answering.
3. **React Frontend:** Displays exceptions in a simple table with filters for reason code and location.
4. **PostgreSQL Tenant Isolation:** Uses Row-Level Security (RLS) on the `exceptions` and `locations` tables to enforce organization-level access at the database level.
5. **Transaction-Local Organization Context:** Sets the active organization within each database transaction instead of depending only on application-level filters.

The reconciliation logic also handles inconsistent System B references, multiple entries for the same record, amount differences, and posting-date delays.

Valid split entries are accepted when their amounts add up to the System A total. Duplicate entries are flagged, while date differences within the configured two-day posting window are treated as acceptable posting delays.

## 4. Scope and Future Improvements
The current implementation focuses on the core reconciliation workflow, tenant isolation, and reliable exception reporting. The following capabilities can be considered for future enhancements:

1. **CSV Upload and Exception Management:** Add a UI for uploading CSV files, correcting exceptions manually, and maintaining an audit trail of changes.
2. **Authentication and User Management:** Integrate production-ready authentication, role-based access control, user registration, and password recovery.
3. **Advanced Analytics:** Extend the question endpoint to support more flexible natural-language queries and advanced reporting.
4. **Production Readiness:** Add cloud deployment, centralized logging, monitoring, automated backups, and scalability improvements.
5. **Configurable Reconciliation Rules:** Allow administrators to configure reference normalization, posting-date tolerances, and reconciliation rules for different source systems.

## 5. How I Worked with the Agent
I used an AI coding agent to review the supplied CSV files, identify reconciliation edge cases, and speed up the initial implementation. It was particularly helpful with repetitive data-cleaning tasks and reviewing the database policies.

However, I didn't rely on its suggestions without checking them. Some assumptions about the data and database behavior were incorrect, so I verified them against the actual CSV records and PostgreSQL queries.

One important issue I caught was in the Row-Level Security policy setup. I corrected the policy initialization and tested the behavior directly against PostgreSQL rather than relying only on application-level checks.

I also tested unsupported questions and cross-organization access to make sure the application refuses requests when it cannot provide a reliable answer.