from fastapi.testclient import TestClient

from app import app
from db import get_connection, set_org_context

client = TestClient(app)


def test_login_and_me():
    response = client.post("/api/login", json={"username": "dealer-a", "password": "dealeros123"})
    assert response.status_code == 200
    body = response.json()
    assert body["organization_id"] == "ORG-A"
    assert "dealeros_session" in response.cookies
    assert "dealeros_org" not in response.cookies

    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["username"] == "dealer-a"


def test_client_cannot_choose_another_organization():
    client.post("/api/login", json={"username": "dealer-a", "password": "dealeros123"})
    client.cookies.set("dealeros_org", "ORG-B")

    response = client.get("/api/exceptions")
    assert response.status_code == 200
    record_ids = {row["record_id"] for row in response.json()["results"]}
    assert record_ids
    assert record_ids.issubset(
        {
            "REC-1015",
            "REC-1027",
            "REC-1042",
            "REC-1064",
            "REC-1077",
            "REC-1088",
            "REC-1999",
        }
    )

    client.post("/api/logout")
    assert client.get("/api/exceptions").status_code == 401

    forged_client = TestClient(
        app,
        cookies={"dealeros_session": "forged-session-token"},
    )
    assert forged_client.get("/api/exceptions").status_code == 401
    client.cookies.clear()


def test_database_rls_enforces_org_boundary():
    with get_connection() as conn:
        set_org_context(conn, "ORG-A")
        with conn.cursor() as cur:
            cur.execute("SELECT org_id, record_id FROM exceptions ORDER BY record_id")
            org_a_rows = cur.fetchall()
        assert {row["org_id"] for row in org_a_rows} == {"ORG-A"}

        set_org_context(conn, "ORG-B")
        with conn.cursor() as cur:
            cur.execute("SELECT org_id, record_id FROM exceptions ORDER BY record_id")
            org_b_rows = cur.fetchall()
        assert {row["org_id"] for row in org_b_rows} == {"ORG-B"}


def test_exception_list_is_org_scoped():
    client.post("/api/login", json={"username": "dealer-a", "password": "dealeros123"})
    response = client.get("/api/exceptions")
    assert response.status_code == 200
    rows = response.json()["results"]
    assert rows
    record_ids = {row["record_id"] for row in rows}
    assert record_ids.issubset({
        "REC-1015",
        "REC-1027",
        "REC-1042",
        "REC-1064",
        "REC-1077",
        "REC-1088",
        "REC-1999",
    })


def test_ask_is_grounded_and_refuses_unsupported_question():
    client.post("/api/login", json={"username": "dealer-a", "password": "dealeros123"})
    response = client.post("/api/ask", json={"question": "How many amount mismatches are there?"})
    assert response.status_code == 200
    body = response.json()
    assert "Found" in body["answer"]
    assert len(body["evidence_record_ids"]) == 3
    assert all(record_id in body["answer"] for record_id in body["evidence_record_ids"])

    general_count = client.post("/api/ask", json={"question": "How many exceptions are there?"})
    assert general_count.status_code == 200
    general_body = general_count.json()
    assert len(general_body["evidence_record_ids"]) == 7
    assert all(record_id in general_body["answer"] for record_id in general_body["evidence_record_ids"])

    location_count = client.post(
        "/api/ask",
        json={"question": "How many exceptions are at location 101?"},
    )
    assert location_count.status_code == 200
    location_body = location_count.json()
    assert len(location_body["evidence_record_ids"]) == 2
    assert all(record_id in location_body["answer"] for record_id in location_body["evidence_record_ids"])

    orphan_entries = client.post(
        "/api/ask",
        json={"question": "Which entries are without a System A record?"},
    )
    assert orphan_entries.status_code == 200
    orphan_body = orphan_entries.json()
    assert orphan_body["evidence_record_ids"] == ["REC-1999"]
    assert "ENT/2026/4901" in orphan_body["answer"]

    for question in (
        "What is the average?",
        "How many blue vehicles are there?",
        "How many exceptions are blue?",
        "Which amount mismatches happened in January?",
    ):
        refusal = client.post("/api/ask", json={"question": question})
        assert refusal.status_code == 200
        assert refusal.json()["refused"] is True


def test_ask_answers_supported_questions_with_row_evidence():
    client.post("/api/login", json={"username": "dealer-a", "password": "dealeros123"})

    total = client.post(
        "/api/ask",
        json={"question": "How many total exceptions are there?"},
    ).json()
    assert "Found 7 matching exception row(s)" in total["answer"]
    assert len(total["evidence_record_ids"]) == 7
    assert all(record_id in total["answer"] for record_id in total["evidence_record_ids"])

    highest_location = client.post(
        "/api/ask",
        json={"question": "Which location has the highest number of exceptions?"},
    ).json()
    assert "Location 102 (LOC-102) has 3 exception rows" in highest_location["answer"]
    assert all(
        record_id in highest_location["answer"]
        for record_id in ("REC-1027", "REC-1077", "REC-1999")
    )

    amount_total = client.post(
        "/api/ask",
        json={"question": "What is the total System A amount for amount mismatches?"},
    ).json()
    assert "401,451.49" in amount_total["answer"]
    assert all(
        record_id in amount_total["answer"]
        for record_id in ("REC-1027", "REC-1064", "REC-1088")
    )

    duplicate_explanation = client.post(
        "/api/ask",
        json={"question": "Why is REC-1042 flagged if both amounts match?"},
    ).json()
    assert "DUPLICATE_B_ENTRY" in duplicate_explanation["answer"]
    assert "ENT/2026/4042" in duplicate_explanation["answer"]
    assert "ENT/2026/4902" in duplicate_explanation["answer"]

    entry_ids = client.post(
        "/api/ask",
        json={"question": "List all System B entry IDs for REC-1042."},
    ).json()
    assert "ENT/2026/4042" in entry_ids["answer"]
    assert "ENT/2026/4902" in entry_ids["answer"]

    difference = client.post(
        "/api/ask",
        json={"question": "What is the difference between the amounts in REC-1027?"},
    ).json()
    assert "17,337.91" in difference["answer"]
    assert "REC-1027" in difference["answer"]
    assert "ENT/2026/4027" in difference["answer"]

    presence = client.post(
        "/api/ask",
        json={"question": "Is REC-1999 present in System A?"},
    ).json()
    assert presence["answer"].startswith("No.")
    assert "REC-1999" in presence["answer"]
    assert "ENT/2026/4901" in presence["answer"]

    for question in (
        "Who is responsible for the mismatch in REC-1064?",
        "What will be the transaction amount next month?",
        "What is the phone number of the customer associated with REC-1015?",
    ):
        response = client.post("/api/ask", json={"question": question})
        assert response.status_code == 200
        assert response.json()["refused"] is True
