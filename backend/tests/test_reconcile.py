import csv
from collections import defaultdict
from pathlib import Path

from reconcile import normalize_record_ref, reconcile


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def read_csv(filename: str) -> list[dict[str, str]]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def test_normalizes_dirty_record_references():
    assert normalize_record_ref(" r-e-c / 10 09 ") == "REC-1009"
    assert normalize_record_ref(" 1055 ") == "REC-1055"
    assert normalize_record_ref("unmatched") is None


def test_accepts_valid_split_and_posting_lag():
    exceptions = reconcile(
        read_csv("system_a.csv"),
        read_csv("system_b.csv"),
        read_csv("locations.csv"),
    )
    reasons_by_record: dict[str, set[str]] = defaultdict(set)
    for exception in exceptions:
        reasons_by_record[exception["record_id"]].add(exception["reason_code"])

    assert "REC-1055" not in reasons_by_record
    assert "DATE_MISMATCH" not in reasons_by_record["REC-1009"]
    assert reasons_by_record["REC-1042"] == {"DUPLICATE_B_ENTRY"}
