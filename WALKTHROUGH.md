# DealerOS Reconciliation Walkthrough

## What the application does

DealerOS compares records from System A with entries from System B and highlights cases where the two systems don't agree.

Each demo user belongs to a specific organization. For example, `dealer-a` belongs to `ORG-A`, and `dealer-b` belongs to `ORG-B`. PostgreSQL Row-Level Security (RLS) makes sure users can only access records belonging to their organization.

The reconciliation process handles issues such as inconsistent System B references, multiple entries for the same record, amount mismatches, and date differences.

Not every difference is treated as an error. For example, when multiple System B entries add up to the System A amount, they are considered a valid split. However, repeated entries with the same amount are flagged as duplicates. Date differences within the configured two-day posting window are also treated as normal posting delays.

## Running the application

Follow the setup instructions in the README to start the application. Once it's running, open:

http://localhost:5173

Sign in using:

- Username: `dealer-a`
- Password: `dealeros123`

After signing in, you'll see the exceptions belonging to that organization. You can filter the results by reason code and location.

You can also sign out and log in as `dealer-b` to check that the second organization sees only its own records.

## Asking questions about exceptions

The application includes a question endpoint that answers a limited set of questions using the imported exception data.

For example, if `dealer-a` asks:

"How many total exceptions are there?"

The API returns 7 exceptions along with the supporting record IDs:

- `REC-1015`
- `REC-1027`
- `REC-1042`
- `REC-1064`
- `REC-1077`
- `REC-1088`
- `REC-1999`

If you ask why `REC-1042` is flagged, the API explains that System B contains two entries: `ENT/2026/4042` and `ENT/2026/4902`. Both have the same amount as System A, so the record is flagged because of a duplicate entry, not an amount mismatch.

The endpoint also handles questions that cannot be answered from the available data. For example, it won't predict future transaction amounts, identify who caused a mismatch, or provide customer contact details that aren't present in the CSV files.

I kept the question logic deterministic instead of integrating an external AI model. This makes it easier to verify answers and trace them back to the original records.

## Tenant isolation test

Tenant isolation is enforced directly by PostgreSQL using Row-Level Security, rather than relying only on filters in the application code.

To verify this, I wrote a test that runs raw SQL queries without organization filters. The database should still return only the records belonging to the active organization.

Run the test using:

```powershell
docker compose exec backend python -m pytest -q tests/test_api.py::test_database_rls_enforces_org_boundary
```

To demonstrate why database-level protection matters, you can temporarily disable RLS in a disposable local database:

```powershell
docker compose exec db psql -U dealeros_admin -d dealeros -c "ALTER TABLE exceptions DISABLE ROW LEVEL SECURITY; ALTER TABLE locations DISABLE ROW LEVEL SECURITY;"
```

Run the same test again:

```powershell
docker compose exec backend python -m pytest -q tests/test_api.py::test_database_rls_enforces_org_boundary
```

This time, the test should fail because the unfiltered queries can access records from both organizations.

After that, restore RLS:

```powershell
docker compose exec db psql -U dealeros_admin -d dealeros -c "ALTER TABLE exceptions ENABLE ROW LEVEL SECURITY; ALTER TABLE exceptions FORCE ROW LEVEL SECURITY; ALTER TABLE locations ENABLE ROW LEVEL SECURITY; ALTER TABLE locations FORCE ROW LEVEL SECURITY;"
```

Finally, run the test once more to confirm that tenant isolation is working again:

```powershell
docker compose exec backend python -m pytest -q tests/test_api.py::test_database_rls_enforces_org_boundary
```

**Note:** These RLS changes should only be tested against a disposable local database. Never disable tenant protection on a shared or production database.

## What I kept out of scope

I focused on completing the reconciliation workflow and proving tenant isolation rather than building a full production platform.

The application uses predefined demo users, imports the supplied CSV files, and supports a limited set of evidence-backed questions.

I did not include user administration, CSV upload and repair screens, a dedicated exception resolution workflow, LLM-based analytics, or production deployment and operations.