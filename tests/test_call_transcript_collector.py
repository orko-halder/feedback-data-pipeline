"""
Unit tests for call_transcript_collector.py -- no AWS, no network.
Fixtures are the REAL saved API payloads in docs/api-responses/ wherever possible.
Run: python3 tests/test_call_transcript_collector.py
"""

import copy
import io
import json
import os
import sys
import types

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "lambda"))
sys.modules.setdefault("boto3", types.ModuleType("boto3"))  # handler clients are faked below

import call_transcript_collector as m  # noqa: E402

FAILURES = []
API = os.path.join(HERE, "..", "docs", "api-responses")


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        FAILURES.append(label)


def load(name):
    with open(os.path.join(API, name), encoding="utf-8") as f:
        d = json.load(f)
    return d.get("response", d)


TRANSCRIPT = load("transcribe_transcript_output.json")
_job = load("transcribe_get_transcription_job.json")
JOB = _job.get("TranscriptionJob", _job)
EVENT = load("transcribe_eventbridge_event.json")
B_REQUEST = load("comprehend_compare_design_b_request.json")
B_RESPONSE = load("comprehend_compare_design_b_response.json")


def real_turns():
    return m.merge_turns(TRANSCRIPT["results"]["audio_segments"])


def T(*speakers):
    return [{"speaker": s, "text": f"text {i}"} for i, s in enumerate(speakers)]


# ---------------------------------------------------------------- pure helpers


def test_helpers():
    check(
        "job_status COMPLETED", m.job_status({"TranscriptionJobStatus": "COMPLETED"}) == "COMPLETED"
    )
    check("job_status FAILED", m.job_status({"TranscriptionJobStatus": "FAILED"}) == "FAILED")
    check(
        "job_status IN_PROGRESS -> None",
        m.job_status({"TranscriptionJobStatus": "IN_PROGRESS"}) is None,
    )
    check("job_status missing -> None", m.job_status({}) is None)

    check(
        "parse_s3_uri s3://",
        m.parse_s3_uri("s3://b/raw-data/calls/c.mp3") == ("b", "raw-data/calls/c.mp3"),
    )
    check(
        "parse_s3_uri path-style",
        m.parse_s3_uri("https://s3.us-east-1.amazonaws.com/b/k/x.json") == ("b", "k/x.json"),
    )
    check(
        "parse_s3_uri virtual-hosted",
        m.parse_s3_uri("https://b.s3.us-east-1.amazonaws.com/k/x.json") == ("b", "k/x.json"),
    )
    check(
        "real job MediaFileUri",
        m.parse_s3_uri(JOB["Media"]["MediaFileUri"])[1] == "raw-data/calls/call_001.mp3",
    )
    check(
        "real job TranscriptFileUri",
        m.parse_s3_uri(JOB["Transcript"]["TranscriptFileUri"])[1] == "transcriptions/call_001.json",
    )

    check("stem_of", m.stem_of("raw-data/calls/call_001.mp3") == "call_001")
    check("truncate_bytes under limit unchanged", m.truncate_bytes("hello", 10) == "hello")
    check("truncate_bytes never splits a character", m.truncate_bytes("café", 4) == "caf")

    check("batch_chunks [] -> []", m.batch_chunks([]) == [])
    check("batch_chunks 25 -> one batch", [len(b) for b in m.batch_chunks(list(range(25)))] == [25])
    check(
        "batch_chunks 26 -> [25, 1]", [len(b) for b in m.batch_chunks(list(range(26)))] == [25, 1]
    )


# ---------------------------------------------------------------- turns & roles


def test_turns_and_roles():
    segs = [
        {"speaker_label": "spk_0", "transcript": "Hello.", "start_time": "0.0", "end_time": "1.0"},
        {
            "speaker_label": "spk_0",
            "transcript": "How can I help?",
            "start_time": "1.0",
            "end_time": "2.0",
        },
        {"speaker_label": "spk_1", "transcript": "  ", "start_time": "2.0", "end_time": "2.5"},
        {"speaker_label": "spk_1", "transcript": "Refund.", "start_time": "2.5", "end_time": "3.0"},
    ]
    turns = m.merge_turns(segs)
    check("consecutive same-speaker segments merge", turns[0]["text"] == "Hello. How can I help?")
    check("merged turn keeps last end_time", turns[0]["end_time"] == 2.0)
    check("blank segment skipped", len(turns) == 2 and turns[1]["text"] == "Refund.")

    real = real_turns()
    check("real transcript -> 5 turns", len(real) == 5)
    check(
        "real turns alternate spk_0/spk_1",
        [t["speaker"] for t in real] == ["spk_0", "spk_1"] * 2 + ["spk_0"],
    )

    r = m.infer_roles(T("spk_0", "spk_1", "spk_0"))
    check("2 speakers: first = agent", r["spk_0"]["role_guess"] == "agent")
    check("2 speakers: second = customer", r["spk_1"]["role_guess"] == "customer")
    check("2 speakers: method first_speaker", r["spk_1"]["role_method"] == "first_speaker")
    r = m.infer_roles(T("spk_0"))
    check("1 speaker (voicemail) -> unknown", r["spk_0"]["role_guess"] == "unknown")
    r = m.infer_roles(T("spk_0", "spk_1", "spk_2"))
    check("3 speakers -> all unknown", {v["role_guess"] for v in r.values()} == {"unknown"})
    check(
        "3 speakers: method records why",
        r["spk_2"]["role_method"] == "not_inferred:speaker_count=3",
    )
    check("no turns -> {}", m.infer_roles([]) == {})


# ---------------------------------------------------------------- design B docs


def test_pack_turns():
    check("empty -> []", m.pack_turns([], 100) == [])
    c = m.pack_turns(["aaaa", "bbbb"], 100)
    check(
        "all fit -> one chunk joined by space",
        len(c) == 1 and c[0]["text"] == "aaaa bbbb" and c[0]["n_turns"] == 2,
    )
    c = m.pack_turns(["a" * 60, "b" * 60], 100)
    check(
        "split only at turn boundary",
        [x["text"][0] for x in c] == ["a", "b"] and not any(x["truncated"] for x in c),
    )
    c = m.pack_turns(["short", "x" * 150, "after"], 100)
    check(
        "oversized single turn -> own chunk, truncated flag",
        c[1]["truncated"] is True and len(c[1]["text"]) == 100,
    )
    check("turn after a truncated chunk starts a new chunk", c[2]["text"] == "after")
    check("chunks around it untouched", c[0]["text"] == "short" and c[0]["truncated"] is False)
    c = m.pack_turns(["é" * 60], 100)  # 120 bytes, 2 bytes per char
    check(
        "multibyte oversized turn cut to <= limit BYTES", len(c[0]["text"].encode("utf-8")) <= 100
    )
    c = m.pack_turns(["é" * 30, "é" * 30], 100)  # 60 + 1 + 60 bytes > 100
    check("byte limit (not char limit) decides the split", len(c) == 2)


def test_build_sentiment_docs():
    turns = real_turns()
    docs = m.build_sentiment_docs(turns, m.infer_roles(turns))
    check(
        "real call -> 3 docs in slot order",
        [d["slot"] for d in docs] == ["customer_all", "agent_all", "customer_end"],
    )
    check(
        "docs == the exact TextList tested against real Comprehend (design B)",
        [d["text"] for d in docs] == B_REQUEST["TextList"],
    )
    check("customer_all used 2 turns, agent_all 3", [d["n_turns"] for d in docs[:2]] == [2, 3])
    check("roles unknown -> [] (send nothing)", m.build_sentiment_docs(turns, {}) == [])
    t3 = T("spk_0", "spk_1", "spk_2")
    check("3-speaker call -> []", m.build_sentiment_docs(t3, m.infer_roles(t3)) == [])


# ---------------------------------------------------------------- results


def scores(pos=0.0, neg=0.0, neu=0.0, mix=0.0):
    return {"Positive": pos, "Negative": neg, "Neutral": neu, "Mixed": mix}


def test_apply_batch_results():
    items = [{} for _ in range(28)]
    resp = {
        "ResultList": [
            {"Index": 0, "Sentiment": "POSITIVE", "SentimentScore": scores(pos=1)},
            {"Index": 2, "Sentiment": "NEGATIVE", "SentimentScore": scores(neg=1)},
        ],
        "ErrorList": [{"Index": 1, "ErrorCode": "INTERNAL_SERVER_ERROR"}],
    }
    errors = m.apply_batch_results(items, 25, resp)
    check(
        "result lands at offset + Index (not list position)", items[27]["sentiment"] == "NEGATIVE"
    )
    check(
        "gap filled by ErrorList item",
        items[26]["sentiment"] is None and items[26]["sentiment_error"] == "INTERNAL_SERVER_ERROR",
    )
    check(
        "error index includes offset", errors == [{"index": 26, "error": "INTERNAL_SERVER_ERROR"}]
    )


def test_call_sentiment_summary():
    turns = real_turns()
    docs = m.build_sentiment_docs(turns, m.infer_roles(turns))
    m.apply_batch_results(docs, 0, B_RESPONSE)
    s = m.call_sentiment_summary(docs)
    real0 = B_RESPONSE["ResultList"][0]["SentimentScore"]
    check(
        "single part passes Comprehend scores through unchanged",
        all(abs(s["customer_overall"]["scores"][k] - real0[k]) < 1e-12 for k in real0),
    )
    check(
        "real call: customer NEGATIVE, agent POSITIVE, end NEGATIVE",
        [s[k]["sentiment"] for k in ("customer_overall", "agent_overall", "customer_end_state")]
        == ["NEGATIVE", "POSITIVE", "NEGATIVE"],
    )
    check("method recorded", s["customer_overall"]["method"] == "comprehend_speaker_concat")

    split = [  # 300-byte NEGATIVE part + 100-byte POSITIVE part -> 0.75 / 0.25
        {
            "slot": "customer_all",
            "part": 0,
            "text": "n" * 300,
            "n_turns": 3,
            "truncated": False,
            "sentiment_scores": scores(neg=1.0),
        },
        {
            "slot": "customer_all",
            "part": 1,
            "text": "p" * 100,
            "n_turns": 1,
            "truncated": True,
            "sentiment_scores": scores(pos=1.0),
        },
    ]
    c = m.call_sentiment_summary(split)["customer_overall"]
    check(
        "parts combined by BYTE weight (0.75 / 0.25)",
        abs(c["scores"]["Negative"] - 0.75) < 1e-12 and abs(c["scores"]["Positive"] - 0.25) < 1e-12,
    )
    check("label = highest combined score", c["sentiment"] == "NEGATIVE")
    check("truncated in any part propagates", c["truncated"] is True)
    check("turns_used sums parts", c["turns_used"] == 4 and c["parts"] == 2)

    failed = [
        {
            "slot": "customer_all",
            "part": 0,
            "text": "x",
            "n_turns": 1,
            "truncated": False,
            "sentiment": None,
        }
    ]
    s = m.call_sentiment_summary(failed)
    check(
        "all parts failed -> null + comprehend_error",
        s["customer_overall"] == {"sentiment": None, "reason": "comprehend_error"},
    )
    check(
        "slot with no docs -> null + no_text_for_role",
        s["agent_overall"]["reason"] == "no_text_for_role",
    )


def test_failure_record():
    r = m.build_failure_record(
        {
            "TranscriptionJobName": "j",
            "FailureReason": "bad media",
            "Media": {"MediaFileUri": "s3://b/raw-data/calls/c.mp3"},
        }
    )
    check(
        "failure reason passed through",
        r["failure_reason"] == "bad media" and r["status"] == "FAILED",
    )
    r = m.build_failure_record({"TranscriptionJobName": "j"})
    check("missing fields don't crash", r["failure_reason"] == "not provided")


# ---------------------------------------------------------------- handler


class Fake:
    """Stands in for a boto3 client: records every call, returns canned responses."""

    def __init__(self, **responses):
        self.calls = []
        self.responses = responses

    def __getattr__(self, name):
        def method(**kwargs):
            self.calls.append((name, kwargs))
            r = self.responses.get(name)
            return r(**kwargs) if callable(r) else r

        return method


def run_handler(event, job):
    fakes = {
        "transcribe": Fake(get_transcription_job={"TranscriptionJob": job}),
        "s3": Fake(
            get_object=lambda **kw: {"Body": io.BytesIO(json.dumps(TRANSCRIPT).encode())},
            put_object={},
        ),
        "comprehend": Fake(batch_detect_sentiment=B_RESPONSE),
        "cloudwatch": Fake(put_metric_data={}),
    }
    m.boto3.client = lambda name, **kw: fakes[name]
    return m.lambda_handler(event, None), fakes


def names(fake):
    return [c[0] for c in fake.calls]


def test_handler():
    out, f = run_handler(copy.deepcopy(EVENT), copy.deepcopy(JOB))
    check(
        "real event: job looked up by name from the thin event",
        f["transcribe"].calls[0][1]["TranscriptionJobName"]
        == EVENT["detail"]["TranscriptionJobName"],
    )
    check(
        "transcript read from TranscriptFileUri key",
        f["s3"].calls[0]
        == (
            "get_object",
            {"Bucket": "customer-feedback-analysis", "Key": "transcriptions/call_001.json"},
        ),
    )
    sent = f["comprehend"].calls[0][1]
    check(
        "ONE Comprehend call, design B TextList",
        names(f["comprehend"]) == ["batch_detect_sentiment"]
        and sent["TextList"] == B_REQUEST["TextList"],
    )
    put = [c[1] for c in f["s3"].calls if c[0] == "put_object"][0]
    check(
        "output key processed-data/calls/<stem>_processed.json",
        put["Key"] == f"{m.OUTPUT_PREFIX}/call_001_processed.json",
    )
    rec = json.loads(put["Body"])
    check(
        "record carries sentiment summary",
        rec["sentiment"]["customer_overall"]["sentiment"] == "NEGATIVE",
    )
    check("record has no speaker_trajectory", "speaker_trajectory" not in rec)
    metric = f["cloudwatch"].calls[0][1]["MetricData"][0]
    check(
        "metric TranscriptionJobs Status=COMPLETED",
        metric["MetricName"] == "TranscriptionJobs"
        and metric["Dimensions"][0]["Value"] == "COMPLETED",
    )
    check("handler returns processed key", json.loads(out["body"])["processed"] == put["Key"])

    ev = copy.deepcopy(EVENT)
    ev["detail"]["TranscriptionJobStatus"] = "FAILED"
    job = copy.deepcopy(JOB)
    job.update(TranscriptionJobStatus="FAILED", FailureReason="The media format is not supported")
    out, f = run_handler(ev, job)
    put = [c[1] for c in f["s3"].calls if c[0] == "put_object"][0]
    check(
        "FAILED -> record under _failed/",
        put["Key"] == f"{m.FAILED_PREFIX}/{job['TranscriptionJobName']}.json",
    )
    check(
        "FAILED -> no transcript read, no Comprehend",
        "get_object" not in names(f["s3"]) and f["comprehend"].calls == [],
    )
    check(
        "FAILED metric still published (failure RATE)",
        f["cloudwatch"].calls[0][1]["MetricData"][0]["Dimensions"][0]["Value"] == "FAILED",
    )

    ev = copy.deepcopy(EVENT)
    ev["detail"]["TranscriptionJobStatus"] = "IN_PROGRESS"
    out, f = run_handler(ev, copy.deepcopy(JOB))
    check("IN_PROGRESS -> ignored, zero AWS calls", all(x.calls == [] for x in f.values()))


if __name__ == "__main__":
    test_helpers()
    test_turns_and_roles()
    test_pack_turns()
    test_build_sentiment_docs()
    test_apply_batch_results()
    test_call_sentiment_summary()
    test_failure_record()
    test_handler()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
