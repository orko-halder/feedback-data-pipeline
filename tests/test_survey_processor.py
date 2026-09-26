"""
Unit tests for processing/survey_processor.py -- no AWS, no network.
The end-to-end checks run against the REAL data/raw/surveys/surveys.csv.
Run: python3 tests/test_survey_processor.py
"""

import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "processing"))

import survey_processor as sp  # noqa: E402

FAILURES = []
CSV = os.path.join(HERE, "..", "data", "raw", "surveys", "surveys.csv")


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def row(**over):
    base = {
        "customer_id": "CUST-1",
        "product_id": "EAR-2200",
        "survey_date": "2026-01-15",
        "product_rating": "4",
        "service_rating": "3",
        "overall_satisfaction": "Satisfied",
        "improvement_area": "Packaging",
        "comments": "Good.",
    }
    base.update(over)
    return base


def severities(r, check_name):
    return [i["severity"] for i in r["issues"] if i["check"] == check_name]


def test_parsers():
    check("rating '4' -> 4", sp.parse_rating("4") == 4)
    check("rating ' 5 ' tolerates whitespace", sp.parse_rating(" 5 ") == 5)
    check("rating 0 out of range -> None", sp.parse_rating("0") is None)
    check("rating 6 out of range -> None", sp.parse_rating("6") is None)
    check("rating 'five' -> None", sp.parse_rating("five") is None)
    check("rating '' -> None", sp.parse_rating("") is None)
    check("rating None -> None", sp.parse_rating(None) is None)
    check("rating '4.7' -> None (int only, no silent truncation)", sp.parse_rating("4.7") is None)

    check("date ISO round-trips", sp.parse_date("2026-01-15") == "2026-01-15")
    check(
        "date returns a STRING, not datetime (JSON-safe)",
        isinstance(sp.parse_date("2026-01-15"), str),
    )
    check("date '15/01/2026' -> None", sp.parse_date("15/01/2026") is None)
    check("date '2026-13-01' (month 13) -> None", sp.parse_date("2026-13-01") is None)
    check("date '' -> None", sp.parse_date("") is None)


def test_severity_split():
    r = sp.build_record(row(), 2)
    check("clean row: no issues", r["issues"] == [])
    check("clean row: admissible", r["admissible"] is True)

    check(
        "missing customer_id is FATAL",
        severities(sp.build_record(row(customer_id=" "), 2), "customer_id_present") == [sp.FATAL],
    )
    check(
        "bad date is FATAL",
        severities(sp.build_record(row(survey_date="Jan 2026"), 2), "survey_date_valid")
        == [sp.FATAL],
    )
    check(
        "rating out of range is FATAL",
        severities(sp.build_record(row(product_rating="9"), 2), "product_rating_in_1_5")
        == [sp.FATAL],
    )
    check(
        "unknown satisfaction label is FATAL",
        severities(
            sp.build_record(row(overall_satisfaction="Quite Happy"), 2),
            "overall_satisfaction_known_label",
        )
        == [sp.FATAL],
    )

    r = sp.build_record(row(product_id=""), 2)
    check(
        "missing product_id is ADVISORY (joinability, not validity)",
        severities(r, "product_id_present") == [sp.ADVISORY],
    )
    check(
        "product_id carried onto the record", sp.build_record(row(), 2)["product_id"] == "EAR-2200"
    )

    r = sp.build_record(row(comments="", improvement_area=""), 2)
    check("empty comments is ADVISORY only", severities(r, "comments_present") == [sp.ADVISORY])
    check("ADVISORY issues do NOT block admissibility", r["admissible"] is True)
    check("has_comment False when blank", r["has_comment"] is False)

    r = sp.build_record(row(customer_id="", product_rating="0"), 2)
    check("several FATALs all recorded, not just the first", len(r["issues"]) == 2)
    check("any FATAL -> not admissible", r["admissible"] is False)


def test_gap():
    check("p4 s2, 'Satisfied'(4) -> +1.0", sp.rating_satisfaction_gap([4, 2], 4) == 1.0)
    check("p5 s5, 'Very Dissatisfied'(1) -> -4.0", sp.rating_satisfaction_gap([5, 5], 1) == -4.0)
    check("agreement -> 0.0", sp.rating_satisfaction_gap([4, 4], 4) == 0.0)
    check("one rating missing -> uses the other", sp.rating_satisfaction_gap([None, 2], 4) == 2.0)
    check(
        "both ratings missing -> None, NOT 0.0", sp.rating_satisfaction_gap([None, None], 4) is None
    )
    check("no satisfaction label -> None", sp.rating_satisfaction_gap([4, 4], None) is None)
    check("rounded to 2dp", sp.rating_satisfaction_gap([4, 3], 5) == 1.5)

    r = sp.build_record(row(product_rating="x", service_rating="y"), 2)
    check("record-level: unparseable ratings -> gap None", r["rating_satisfaction_gap"] is None)
    check(
        "satisfaction_ordinal maps the label",
        sp.build_record(row(), 2)["satisfaction_ordinal"] == 4,
    )


def test_summary_and_real_csv():
    recs = [sp.build_record(row(), 2), sp.build_record(row(customer_id=""), 3)]
    s = sp.summarise(recs)
    check("summary counts admissible/rejected", (s["admissible"], s["rejected"]) == (1, 1))
    check("admission_rate 0.5", s["admission_rate"] == 0.5)
    check("means EXCLUDE inadmissible rows", s["mean_product_rating"] == 4)
    check("issue_counts tallies by check name", s["issue_counts"]["customer_id_present"] == 1)
    check(
        "empty input -> no crash, admission_rate None", sp.summarise([])["admission_rate"] is None
    )

    out = sp.process_csv(CSV)
    s = out["summary"]
    check("real CSV: 30 data rows", s["rows_read"] == 30)
    check("real CSV: exactly the 2 seeded bad rows rejected", s["rejected"] == 2)
    check(
        "real CSV: rejections are the missing id and the bad date",
        sorted(
            i["check"]
            for r in out["records"]
            if not r["admissible"]
            for i in r["issues"]
            if i["severity"] == sp.FATAL
        )
        == ["customer_id_present", "survey_date_valid"],
    )
    check("row_number starts at 2 (header is row 1)", out["records"][0]["row_number"] == 2)
    check(
        "row_numbers are contiguous",
        [r["row_number"] for r in out["records"]] == list(range(2, 32)),
    )
    check("source_file recorded", out["source_file"] == "surveys.csv")
    check("every record has the same keys", len({tuple(sorted(r)) for r in out["records"]}) == 1)

    import json

    json.dumps(out)  # raises if anything is not JSON-serialisable
    check("whole output is JSON-serialisable", True)


if __name__ == "__main__":
    test_parsers()
    test_severity_split()
    test_gap()
    test_summary_and_real_csv()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
