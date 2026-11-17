import csv
import json
import os
from pathlib import Path

import psycopg

from db import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER, get_connection
from reconcile import REASONS, reconcile

DATA_DIR = Path(__file__).resolve().parent / "data"


def ensure_schema_and_seed() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS organizations (org_id TEXT PRIMARY KEY)")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT NOT NULL, org_id TEXT NOT NULL REFERENCES organizations(org_id))"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS auth_sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username), org_id TEXT NOT NULL REFERENCES organizations(org_id), expires_at TIMESTAMPTZ NOT NULL)"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS locations (location_id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(org_id), location_name TEXT NOT NULL)"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS exceptions (id SERIAL PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(org_id), location_id TEXT NOT NULL, record_id TEXT NOT NULL, reason_code TEXT NOT NULL, summary TEXT NOT NULL, evidence JSONB NOT NULL, UNIQUE (org_id, record_id, reason_code))"
            )
            cur.execute("ALTER TABLE locations ENABLE ROW LEVEL SECURITY")
            cur.execute("ALTER TABLE exceptions ENABLE ROW LEVEL SECURITY")
            cur.execute(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'locations' AND policyname = 'locations_org_policy') THEN CREATE POLICY locations_org_policy ON locations USING (org_id = current_setting('app.current_org_id', true)) WITH CHECK (org_id = current_setting('app.current_org_id', true)); END IF; END $$;"
            )
            cur.execute(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'exceptions' AND policyname = 'exceptions_org_policy') THEN CREATE POLICY exceptions_org_policy ON exceptions USING (org_id = current_setting('app.current_org_id', true)) WITH CHECK (org_id = current_setting('app.current_org_id', true)); END IF; END $$;"
            )
            cur.execute("ALTER TABLE locations FORCE ROW LEVEL SECURITY")
            cur.execute("ALTER TABLE exceptions FORCE ROW LEVEL SECURITY")
            cur.execute("INSERT INTO organizations (org_id) VALUES ('ORG-A') ON CONFLICT (org_id) DO NOTHING")
            cur.execute("INSERT INTO organizations (org_id) VALUES ('ORG-B') ON CONFLICT (org_id) DO NOTHING")
            cur.execute(
                "INSERT INTO users (username, password, org_id) VALUES ('dealer-a', 'dealeros123', 'ORG-A') ON CONFLICT (username) DO UPDATE SET password = EXCLUDED.password, org_id = EXCLUDED.org_id"
            )
            cur.execute(
                "INSERT INTO users (username, password, org_id) VALUES ('dealer-b', 'dealeros123', 'ORG-B') ON CONFLICT (username) DO UPDATE SET password = EXCLUDED.password, org_id = EXCLUDED.org_id"
            )
        conn.commit()
        import_csvs()
    finally:
        conn.close()


def import_csvs() -> None:
    location_rows = read_csv(DATA_DIR / "locations.csv")
    system_a = read_csv(DATA_DIR / "system_a.csv")
    system_b = read_csv(DATA_DIR / "system_b.csv")
    exceptions = reconcile(system_a, system_b, location_rows)

    with get_connection() as conn:
        with conn.cursor() as cur:
            for row in location_rows:
                cur.execute("SELECT set_config('app.current_org_id', %s, true)", [row["org_id"]])
                cur.execute(
                    "INSERT INTO locations (location_id, org_id, location_name) VALUES (%s, %s, %s) ON CONFLICT (location_id) DO UPDATE SET org_id = EXCLUDED.org_id, location_name = EXCLUDED.location_name",
                    (row["location_id"], row["org_id"], row["location_name"]),
                )

            for item in exceptions:
                cur.execute("SELECT set_config('app.current_org_id', %s, true)", [item["organization_id"]])
                cur.execute(
                    "INSERT INTO exceptions (org_id, location_id, record_id, reason_code, summary, evidence) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (org_id, record_id, reason_code) DO UPDATE SET location_id = EXCLUDED.location_id, summary = EXCLUDED.summary, evidence = EXCLUDED.evidence",
                    (
                        item["organization_id"],
                        item["location_id"],
                        item["record_id"],
                        item["reason_code"],
                        item["summary"],
                        json.dumps(item["evidence"]),
                    ),
                )
        conn.commit()


def read_csv(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))
