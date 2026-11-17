import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation


POSTING_LAG_DAYS = 2
REASONS = {
    "MISSING_B_ENTRY": "No System B entry exists. Check whether the event was exported.",
    "ORPHAN_B_ENTRY": "System B refers to a record missing from System A. Check the source reference.",
    "MISSING_B_AMOUNT": "A System B entry has no amount. Ask the source team to complete it.",
    "AMOUNT_MISMATCH": "The System B amount does not equal the System A total. Check the amount.",
    "DATE_MISMATCH": "The recorded dates differ by more than two days. Confirm the event date.",
    "DUPLICATE_B_ENTRY": "System B has duplicate entries for this record. Remove the extra entry.",
    "LOCATION_MISMATCH": "The systems assign this event to different locations. Confirm the correct location.",
    "VOIDED_IN_B": "System B contains an entry for an event marked voided in System A. Check its status.",
}


def normalize_record_ref(reference):
    compact = re.sub(r"[^A-Z0-9]", "", (reference or "").upper())
    match = re.fullmatch(r"(?:REC)?(\d+)", compact)
    return f"REC-{match.group(1)}" if match else None


def parse_amount(value):
    if value is None or not value.strip():
        return None
    try:
        return Decimal(value.replace(",", "").strip())
    except InvalidOperation as error:
        raise ValueError(f"Invalid amount: {value!r}") from error


def reconcile(system_a, system_b, locations):
    location_by_id = {row["location_id"]: row for row in locations}
    system_a_ids = {row["record_id"] for row in system_a}
    entries_by_record = defaultdict(list)
    exceptions = []
    matched_entry_ids = set()

    for entry in system_b:
        record_id = normalize_record_ref(entry.get("record_ref", ""))
        if record_id:
            entries_by_record[record_id].append(entry)

    for event in system_a:
        record_id = event["record_id"]
        entries = entries_by_record.pop(record_id, [])
        location = location_by_id.get(event["location_id"])
        organization_id = location["org_id"] if location else None
        if organization_id is None:
            raise ValueError(f"Unknown System A location: {event['location_id']}")

        reasons = []
        if not entries:
            reasons.append("MISSING_B_ENTRY")
        else:
            matched_entry_ids.update(entry["entry_id"] for entry in entries)
            if event["state"].upper() == "VOIDED":
                reasons.append("VOIDED_IN_B")

            if any(not (entry.get("value") or "").strip() for entry in entries):
                reasons.append("MISSING_B_AMOUNT")
            else:
                amounts = [parse_amount(entry["value"]) for entry in entries]
                amount_sum = sum(amounts, Decimal("0"))
                if amount_sum != parse_amount(event["total_value"]):
                    if len(entries) > 1 and len(set(amounts)) == 1:
                        reasons.append("DUPLICATE_B_ENTRY")
                    else:
                        reasons.append("AMOUNT_MISMATCH")

            if any(entry["location_id"] != event["location_id"] for entry in entries):
                reasons.append("LOCATION_MISMATCH")

            expected_date = date.fromisoformat(event["event_date"])
            if any(
                abs((date.fromisoformat(entry["recorded_on"]) - expected_date).days)
                > POSTING_LAG_DAYS
                for entry in entries
            ):
                reasons.append("DATE_MISMATCH")

        for reason_code in dict.fromkeys(reasons):
            exceptions.append(
                {
                    "organization_id": organization_id,
                    "location_id": event["location_id"],
                    "record_id": record_id,
                    "reason_code": reason_code,
                    "summary": REASONS[reason_code],
                    "evidence": {"system_a": event, "system_b": entries},
                }
            )

    for entry in system_b:
        if entry["entry_id"] in matched_entry_ids:
            continue
        record_id = normalize_record_ref(entry.get("record_ref", ""))
        if record_id in system_a_ids:
            continue
        location = location_by_id.get(entry["location_id"])
        if location is None:
            raise ValueError(f"Unknown System B location: {entry['location_id']}")
        exceptions.append(
            {
                "organization_id": location["org_id"],
                "location_id": entry["location_id"],
                "record_id": record_id or entry.get("record_ref") or entry["entry_id"],
                "reason_code": "ORPHAN_B_ENTRY",
                "summary": REASONS["ORPHAN_B_ENTRY"],
                "evidence": {"system_a": None, "system_b": [entry]},
            }
        )

    return exceptions
