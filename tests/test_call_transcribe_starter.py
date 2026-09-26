"""
Unit tests for call_transcribe_starter.py -- pure functions only, no AWS.
Run: python3 tests/test_call_transcribe_starter.py
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))

from call_transcribe_starter import (  # noqa: E402
    JOB_NAME_MAX,
    build_job_name,
    build_start_job_request,
    derive_output_key,
    media_format_from_key,
    parse_s3_event,
)

FAILURES = []
NOW = datetime(2026, 9, 21, 10, 15, 0, tzinfo=timezone.utc)


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def s3_event(key, bucket="customer-feedback-analysis"):
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


def test_url_decoding():
    """The fix this session found missing in three existing Lambdas."""
    bucket, key = parse_s3_event(s3_event("raw-data/calls/my+call+001.mp3"))
    check("'+' decodes to space", key == "raw-data/calls/my call 001.mp3")
    _, key = parse_s3_event(s3_event("raw-data/calls/call%28final%29.mp3"))
    check("percent-encoding decodes", key == "raw-data/calls/call(final).mp3")
    _, key = parse_s3_event(s3_event("raw-data/calls/call_001.mp3"))
    check("plain key passes through unchanged", key == "raw-data/calls/call_001.mp3")
    check("bucket extracted", bucket == "customer-feedback-analysis")


def test_media_format():
    check("mp3 -> mp3", media_format_from_key("raw-data/calls/call_001.mp3") == "mp3")
    check("uppercase .WAV -> wav", media_format_from_key("raw-data/calls/X.WAV") == "wav")
    check(".txt -> None (unsupported)", media_format_from_key("raw-data/calls/notes.txt") is None)
    check("no extension -> None", media_format_from_key("raw-data/calls/call_001") is None)


def test_job_name():
    name = build_job_name("raw-data/calls/call_001.mp3", NOW)
    check("stem + timestamp", name == "feedback-call_001-20260921T101500")
    check("deterministic for a fixed clock",
          build_job_name("raw-data/calls/call_001.mp3", NOW) == name)

    messy = build_job_name("raw-data/calls/my call (final).mp3", NOW)
    check("disallowed chars replaced", messy == "feedback-my-call--final--20260921T101500")
    check("only [0-9a-zA-Z._-] remain",
          all(c.isalnum() or c in "._-" for c in messy))

    long_name = build_job_name("raw-data/calls/" + "x" * 500 + ".mp3", NOW)
    check(f"500-char stem truncated to <= {JOB_NAME_MAX}", len(long_name) <= JOB_NAME_MAX)
    check("timestamp survives truncation", long_name.endswith("-20260921T101500"))


def test_output_key():
    out = derive_output_key("raw-data/calls/call_001.mp3")
    check("-> transcriptions/call_001.json", out == "transcriptions/call_001.json")
    # Loop safety: output must not land where it would re-trigger this function.
    check("output prefix differs from input prefix", not out.startswith("raw-data/calls/"))
    check("output is .json, not an audio extension", media_format_from_key(out) is None)


def test_request_shape():
    req = build_start_job_request("customer-feedback-analysis", "raw-data/calls/call_001.mp3", NOW)
    check("MediaFileUri is s3://bucket/key",
          req["Media"]["MediaFileUri"] == "s3://customer-feedback-analysis/raw-data/calls/call_001.mp3")
    check("MediaFormat mp3", req["MediaFormat"] == "mp3")
    check("output bucket = input bucket", req["OutputBucketName"] == "customer-feedback-analysis")
    check("speaker labels ON", req["Settings"]["ShowSpeakerLabels"] is True)
    check("max 2 speakers (agent + customer)", req["Settings"]["MaxSpeakerLabels"] == 2)
    check("language en-US", req["LanguageCode"] == "en-US")
    check("unsupported file -> None, no request built",
          build_start_job_request("b", "raw-data/calls/notes.txt", NOW) is None)


if __name__ == "__main__":
    test_url_decoding()
    test_media_format()
    test_job_name()
    test_output_key()
    test_request_shape()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
