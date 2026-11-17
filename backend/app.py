from collections import defaultdict
from decimal import Decimal
import json
import re
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db import (
    create_session,
    get_connection,
    get_current_org_from_cookie,
    get_current_session_from_cookie,
    revoke_session,
    set_org_context,
)
from reconcile import REASONS, parse_amount
from seed import ensure_schema_and_seed

app = FastAPI(title="DealerOS Exceptions API")


@app.on_event("startup")
def startup_event() -> None:
    ensure_schema_and_seed()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEMO_USERS = {
    "dealer-a": {"password": "dealeros123", "org_id": "ORG-A"},
    "dealer-b": {"password": "dealeros123", "org_id": "ORG-B"},
}

REFUSAL_ANSWER = (
    "I can't answer that from the available exception data. "
    "Try asking about exception counts, mismatch totals, or a specific record."
)
REASON_ALIASES = {
    "MISSING_B_ENTRY": (
        "missing entry",
        "missing entries",
        "missing b entry",
        "missing b entries",
        "no system b entry",
        "system b entry missing",
    ),
    "ORPHAN_B_ENTRY": (
        "orphan",
        "orphaned",
        "entry without a system a record",
        "entries without a system a record",
        "entry is without a system a record",
        "entries are without a system a record",
        "entry with no system a record",
        "entries with no system a record",
    ),
    "MISSING_B_AMOUNT": (
        "missing amount",
        "missing amounts",
        "blank amount",
        "blank amounts",
    ),
    "AMOUNT_MISMATCH": ("amount mismatch", "amount mismatches", "wrong amount"),
    "DATE_MISMATCH": ("date mismatch", "date mismatches"),
    "DUPLICATE_B_ENTRY": ("duplicate", "duplicates", "duplicate entry", "duplicate entries"),
    "LOCATION_MISMATCH": ("location mismatch", "location mismatches", "wrong location"),
    "VOIDED_IN_B": ("voided", "voided in system b"),
}
LOCATION_ALIASES = {
    "LOC-101": ("loc 101", "location 101"),
    "LOC-102": ("loc 102", "location 102"),
    "LOC-103": ("loc 103", "location 103"),
    "LOC-201": ("loc 201", "location 201"),
    "LOC-202": ("loc 202", "location 202"),
}
QUESTION_FILLER_WORDS = {
    "a",
    "all",
    "an",
    "are",
    "at",
    "b",
    "by",
    "code",
    "count",
    "do",
    "entries",
    "entry",
    "exception",
    "exceptions",
    "for",
    "have",
    "how",
    "in",
    "is",
    "list",
    "location",
    "loc",
    "many",
    "match",
    "matching",
    "me",
    "number",
    "of",
    "on",
    "or",
    "our",
    "please",
    "record",
    "records",
    "reason",
    "rows",
    "show",
    "system",
    "the",
    "there",
    "total",
    "which",
    "with",
    "would",
}


def coerce_evidence(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if isinstance(value, dict):
        return value
    return {}


def normalized_question_phrase(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def remove_question_phrase(question: str, phrase: str) -> tuple[str, bool]:
    pattern = re.compile(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])")
    return pattern.sub(" ", question), pattern.search(question) is not None


def parse_exception_question(question: str) -> tuple[str | None, str | None] | None:
    remaining = normalized_question_phrase(question)
    remaining, has_intent = remove_question_phrase(remaining, "how many")
    if not has_intent:
        remaining, has_intent = remove_question_phrase(remaining, "number of")
    if not has_intent:
        remaining, has_intent = remove_question_phrase(remaining, "count")
    if not has_intent:
        for intent in ("list", "show", "which"):
            remaining, has_intent = remove_question_phrase(remaining, intent)
            if has_intent:
                break
    if not has_intent:
        return None

    reason_codes = set()
    reason_phrases = [
        (code, normalized_question_phrase(code.replace("_", " ")))
        for code in REASON_ALIASES
    ]
    reason_phrases.extend(
        (code, normalized_question_phrase(alias))
        for code, aliases in REASON_ALIASES.items()
        for alias in aliases
    )
    for code, phrase in sorted(reason_phrases, key=lambda item: len(item[1]), reverse=True):
        remaining, found = remove_question_phrase(remaining, phrase)
        if found:
            reason_codes.add(code)

    location_ids = set()
    location_phrases = [
        (location_id, normalized_question_phrase(location_id))
        for location_id in LOCATION_ALIASES
    ]
    location_phrases.extend(
        (location_id, normalized_question_phrase(alias))
        for location_id, aliases in LOCATION_ALIASES.items()
        for alias in aliases
    )
    for location_id, phrase in sorted(
        location_phrases, key=lambda item: len(item[1]), reverse=True
    ):
        remaining, found = remove_question_phrase(remaining, phrase)
        if found:
            location_ids.add(location_id)

    has_exception_scope = re.search(r"\bexceptions?\b", remaining) is not None
    remaining = re.sub(r"\bexceptions?\b", " ", remaining)
    unmatched_words = [
        word for word in remaining.split() if word not in QUESTION_FILLER_WORDS
    ]
    if unmatched_words or len(reason_codes) > 1 or len(location_ids) > 1:
        return None

    reason_code = next(iter(reason_codes), None)
    location_id = next(iter(location_ids), None)
    if not (has_exception_scope or reason_code or location_id):
        return None
    return reason_code, location_id


def extract_record_id(question: str) -> str | None:
    match = re.search(r"\bREC[-\s]?(\d+)\b", question, re.IGNORECASE)
    return f"REC-{match.group(1)}" if match else None


def fetch_exception_rows(
    org_id: str,
    record_id: str | None = None,
    reason_code: str | None = None,
    location_id: str | None = None,
) -> list[dict[str, Any]]:
    query = (
        "SELECT id, record_id, location_id, reason_code, evidence "
        "FROM exceptions WHERE org_id = %s"
    )
    params: list[Any] = [org_id]
    if record_id:
        query += " AND record_id = %s"
        params.append(record_id)
    if reason_code:
        query += " AND reason_code = %s"
        params.append(reason_code)
    if location_id:
        query += " AND location_id = %s"
        params.append(location_id)
    query += " ORDER BY record_id, reason_code"

    with get_connection() as conn:
        set_org_context(conn, org_id)
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def fetch_location_names(org_id: str, location_ids: set[str]) -> dict[str, str]:
    if not location_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(location_ids))
    with get_connection() as conn:
        set_org_context(conn, org_id)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT location_id, location_name FROM locations "
                f"WHERE location_id IN ({placeholders})",
                list(location_ids),
            )
            return {row["location_id"]: row["location_name"] for row in cur.fetchall()}


def get_system_b_entries(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    entries = evidence.get("system_b") or []
    if isinstance(entries, dict):
        entries = [entries]
    return [entry for entry in entries if isinstance(entry, dict)]


def answer_special_question(org_id: str, question: str) -> dict[str, Any] | None:
    normalized = normalized_question_phrase(question)
    if re.fullmatch(
        r"(?:which|what) location (?:has|have) (?:the )?"
        r"(?:highest|most) (?:number of )?exceptions",
        normalized,
    ):
        rows = fetch_exception_rows(org_id)
        if not rows:
            return {"answer": REFUSAL_ANSWER, "refused": True}
        rows_by_location: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            rows_by_location[row["location_id"]].append(row)
        highest_count = max(len(location_rows) for location_rows in rows_by_location.values())
        highest_locations = {
            location_id: location_rows
            for location_id, location_rows in rows_by_location.items()
            if len(location_rows) == highest_count
        }
        location_names = fetch_location_names(org_id, set(highest_locations))
        details = []
        cited_record_ids = []
        for location_id, location_rows in sorted(highest_locations.items()):
            citations = [
                f"{row['record_id']} [{row['reason_code']}]"
                for row in location_rows
            ]
            cited_record_ids.extend(row["record_id"] for row in location_rows)
            location_name = location_names.get(location_id, location_id)
            details.append(
                f"{location_name} ({location_id}) has {len(location_rows)} exception "
                f"rows: {', '.join(citations)}"
            )
        return {
            "answer": "Highest exception count by location: " + "; ".join(details) + ".",
            "evidence_record_ids": cited_record_ids,
        }

    if re.fullmatch(
        r"(?:what is )?(?:the )?total system a amount "
        r"(?:for|of) amount mismatches",
        normalized,
    ):
        rows = fetch_exception_rows(org_id, reason_code="AMOUNT_MISMATCH")
        source_amounts = []
        for row in rows:
            evidence = coerce_evidence(row["evidence"])
            system_a = evidence.get("system_a")
            if not isinstance(system_a, dict):
                return {"answer": REFUSAL_ANSWER, "refused": True}
            amount = parse_amount(system_a.get("total_value", ""))
            if amount is None:
                return {"answer": REFUSAL_ANSWER, "refused": True}
            source_amounts.append((row["record_id"], amount))
        if not source_amounts:
            return {"answer": REFUSAL_ANSWER, "refused": True}
        total = sum((amount for _, amount in source_amounts), Decimal("0"))
        detail = "; ".join(
            f"{record_id}: {amount:,.2f}" for record_id, amount in source_amounts
        )
        record_ids = [record_id for record_id, _ in source_amounts]
        return {
            "answer": (
                f"System A total for amount mismatches is {total:,.2f}. "
                f"Source amounts: {detail}."
            ),
            "evidence_record_ids": record_ids,
        }

    record_id = extract_record_id(question)
    if not record_id:
        return None
    rows = fetch_exception_rows(org_id, record_id=record_id)
    if not rows:
        return {"answer": REFUSAL_ANSWER, "refused": True}

    evidence = coerce_evidence(rows[0]["evidence"])
    system_a = evidence.get("system_a")
    system_b = get_system_b_entries(evidence)
    system_b_ids = [
        entry["entry_id"]
        for entry in system_b
        if isinstance(entry.get("entry_id"), str) and entry["entry_id"]
    ]

    if re.fullmatch(r"why is rec \d+ flagged(?: if both amounts match)?", normalized):
        duplicate_row = next(
            (row for row in rows if row["reason_code"] == "DUPLICATE_B_ENTRY"),
            None,
        )
        if duplicate_row and isinstance(system_a, dict):
            system_a_amount = parse_amount(system_a.get("total_value", ""))
            entry_amounts = [
                parse_amount(entry.get("value", "")) for entry in system_b
            ]
            if (
                system_a_amount is not None
                and len(system_b) > 1
                and all(amount == system_a_amount for amount in entry_amounts)
            ):
                details = "; ".join(
                    f"{entry.get('entry_id', 'System B entry')} records "
                    f"{parse_amount(entry.get('value', '')):,.2f}"
                    for entry in system_b
                )
                return {
                    "answer": (
                        f"{record_id} is flagged as DUPLICATE_B_ENTRY. System A "
                        f"recorded {system_a_amount:,.2f} for {record_id}; {details}. "
                        "Each System B entry matches individually, but both entries "
                        "duplicate the same event."
                    ),
                    "evidence_record_ids": [record_id],
                }
        explanations = [
            f"{record_id} [{row['reason_code']}]: "
            f"{REASONS.get(row['reason_code'], 'The systems disagree for this record.')}"
            for row in rows
        ]
        return {
            "answer": " ".join(explanations),
            "evidence_record_ids": [record_id],
        }

    if (
        re.fullmatch(
            r"list (?:all )?system b entry ids for rec \d+",
            normalized,
        )
    ):
        if not system_b_ids:
            return {
                "answer": f"No System B entry IDs are recorded for {record_id}.",
                "evidence_record_ids": [record_id],
            }
        return {
            "answer": (
                f"System B entry IDs for {record_id}: {', '.join(system_b_ids)}."
            ),
            "evidence_record_ids": [record_id],
        }

    if re.fullmatch(
        r"(?:what is )?the difference between the amounts in rec \d+",
        normalized,
    ):
        if not isinstance(system_a, dict) or not system_b or len(system_b_ids) != len(system_b):
            return {"answer": REFUSAL_ANSWER, "refused": True}
        system_a_amount = parse_amount(system_a.get("total_value", ""))
        system_b_amounts = [
            parse_amount(entry.get("value", "")) for entry in system_b
        ]
        if system_a_amount is None or any(amount is None for amount in system_b_amounts):
            return {"answer": REFUSAL_ANSWER, "refused": True}
        system_b_total = sum(system_b_amounts, Decimal("0"))
        difference = abs(system_a_amount - system_b_total)
        return {
            "answer": (
                f"For {record_id}, System A total is {system_a_amount:,.2f}. "
                f"System B total across {', '.join(system_b_ids)} is "
                f"{system_b_total:,.2f}. Difference for {record_id} is "
                f"{difference:,.2f}."
            ),
            "evidence_record_ids": [record_id],
        }

    if re.fullmatch(r"is rec \d+ present in system a", normalized):
        if "system_a" not in evidence:
            return {"answer": REFUSAL_ANSWER, "refused": True}
        if system_a is None:
            return {
                "answer": (
                    f"No. {record_id} is an ORPHAN_B_ENTRY; System A has no matching "
                    f"record, while System B entry IDs {', '.join(system_b_ids)} "
                    f"reference {record_id}."
                ),
                "evidence_record_ids": [record_id],
            }
        if not isinstance(system_a, dict):
            return {"answer": REFUSAL_ANSWER, "refused": True}
        return {
            "answer": f"Yes. System A contains {record_id}.",
            "evidence_record_ids": [record_id],
        }

    if re.fullmatch(
        r"who is responsible for the mismatch in rec \d+",
        normalized,
    ):
        return {
            "answer": (
                f"I can't identify who is responsible for the mismatch in {record_id} "
                "from the available evidence."
            ),
            "refused": True,
            "evidence_record_ids": [record_id],
        }
    return None


class LoginRequest(BaseModel):
    username: str
    password: str


class AskRequest(BaseModel):
    question: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/login")
def login(payload: LoginRequest, response: Response):
    user = DEMO_USERS.get(payload.username)
    if not user or user["password"] != payload.password:
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    token = create_session(payload.username, user["org_id"])
    response.set_cookie(
        key="dealeros_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=8 * 60 * 60,
    )
    return {"username": payload.username, "organization_id": user["org_id"]}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    revoke_session(request.cookies.get("dealeros_session"))
    response.delete_cookie("dealeros_session")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    try:
        session = get_current_session_from_cookie(request)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail="Login required.") from exc
    return {"username": session["username"], "organization_id": session["org_id"]}


@app.get("/api/filters")
def filters(request: Request):
    try:
        org_id = get_current_org_from_cookie(request)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail="Login required.") from exc
    with get_connection() as conn:
        set_org_context(conn, org_id)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT location_id, location_name FROM locations ORDER BY location_id"
            )
            locations = [{"id": row["location_id"], "name": row["location_name"]} for row in cur.fetchall()]
    return {
        "locations": locations,
        "reasons": [{"code": code, "label": label} for code, label in REASONS.items()],
    }


@app.get("/api/exceptions")
def exceptions_view(request: Request, reason: str | None = None, location: str | None = None):
    try:
        org_id = get_current_org_from_cookie(request)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail="Login required.") from exc
    query = (
        "SELECT id, location_id, record_id, reason_code, summary, evidence "
        "FROM exceptions WHERE org_id = %s"
    )
    params: list[Any] = [org_id]
    if reason:
        query += " AND reason_code = %s"
        params.append(reason)
    if location:
        query += " AND location_id = %s"
        params.append(location)
    query += " ORDER BY record_id, reason_code"

    with get_connection() as conn:
        set_org_context(conn, org_id)
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    results = []
    for row in rows:
        item_id = row["id"]
        location_id = row["location_id"]
        record_id = row["record_id"]
        reason_code = row["reason_code"]
        summary = row["summary"]
        evidence = row["evidence"]
        evidence_obj = coerce_evidence(evidence)
        system_a = evidence_obj.get("system_a") if isinstance(evidence_obj, dict) else None
        system_b = evidence_obj.get("system_b") if isinstance(evidence_obj, dict) else []
        if isinstance(system_b, dict):
            system_b = [system_b]
        results.append(
            {
                "id": item_id,
                "record_id": record_id,
                "reason_code": reason_code,
                "reason": REASONS.get(reason_code, summary),
                "location_id": location_id,
                "location_name": "Unknown",
                "system_a": system_a,
                "system_b": system_b,
            }
        )

    if results:
        location_ids = {item["location_id"] for item in results if item["location_id"]}
        if location_ids:
            in_clause = ", ".join(["%s"] * len(location_ids))
            with get_connection() as conn:
                set_org_context(conn, org_id)
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT location_id, location_name FROM locations WHERE location_id IN ({in_clause}) ORDER BY location_id",
                        list(location_ids),
                    )
                    names = {row["location_id"]: row["location_name"] for row in cur.fetchall()}
            for item in results:
                item["location_name"] = names.get(item["location_id"], "Unknown")

    return {"results": results}


@app.post("/api/ask")
def ask(payload: AskRequest, request: Request):
    try:
        org_id = get_current_org_from_cookie(request)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail="Login required.") from exc
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required.")

    special_answer = answer_special_question(org_id, question)
    if special_answer is not None:
        return special_answer

    parsed_question = parse_exception_question(question)
    if parsed_question is None:
        return {"answer": REFUSAL_ANSWER, "refused": True}
    reason_code, location_id = parsed_question

    rows = fetch_exception_rows(
        org_id,
        reason_code=reason_code,
        location_id=location_id,
    )

    if not rows:
        return {"answer": REFUSAL_ANSWER, "refused": True}

    citations = []
    for row in rows:
        record_id = row["record_id"]
        evidence_obj = coerce_evidence(row["evidence"])
        system_b_entries = get_system_b_entries(evidence_obj)
        entry_ids = [
            entry["entry_id"]
            for entry in system_b_entries
            if isinstance(entry.get("entry_id"), str) and entry["entry_id"]
        ]
        citations.append(
            f"{record_id} [{row['reason_code']}] "
            f"(System B: {', '.join(entry_ids) if entry_ids else 'no entry'})"
        )

    answer = f"Found {len(rows)} matching exception row(s). Evidence: {'; '.join(citations)}."
    return {
        "answer": answer,
        "evidence_record_ids": [row["record_id"] for row in rows],
    }
