import hashlib
import os
import secrets
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row


DB_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
DB_PORT = os.getenv("POSTGRES_PORT", "5433")
DB_NAME = os.getenv("POSTGRES_DB", "dealeros")
DB_USER = os.getenv("POSTGRES_USER", "dealeros_app")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "dealeros_local")


def get_connection():
    return psycopg.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        row_factory=dict_row,
    )


@contextmanager
def session_scope(org_id: str | None = None) -> Iterator[psycopg.Connection]:
    conn = get_connection()
    if org_id is not None:
        conn.execute("SELECT set_config('app.current_org_id', %s, true)", [org_id])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_session(username: str, org_id: str) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO auth_sessions (token_hash, username, org_id, expires_at) "
            "VALUES (%s, %s, %s, CURRENT_TIMESTAMP + INTERVAL '8 hours')",
            (token_hash, username, org_id),
        )
    return token


def get_current_session_from_cookie(request) -> dict[str, Any]:
    token = request.cookies.get("dealeros_session")
    if not token:
        raise PermissionError("Login required.")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username, org_id FROM auth_sessions "
                "WHERE token_hash = %s AND expires_at > CURRENT_TIMESTAMP",
                (token_hash,),
            )
            session = cur.fetchone()
    if session is None:
        raise PermissionError("Login required.")
    return session


def get_current_org_from_cookie(request) -> str:
    return get_current_session_from_cookie(request)["org_id"]


def revoke_session(token: str | None) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with get_connection() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (token_hash,))


def set_org_context(conn, org_id: str):
    conn.execute("SELECT set_config('app.current_org_id', %s, true)", [org_id])
