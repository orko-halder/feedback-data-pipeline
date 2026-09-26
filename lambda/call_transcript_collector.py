"""
Transcribe COLLECTOR -- turns a finished transcription job into per-turn,
speaker-attributed sentiment.

Triggered by EventBridge "Transcribe Job State Change" (COMPLETED or FAILED),
not by S3. The starter Lambda exited long before the job finished; the only
thing that knows the job is done is Transcribe, and EventBridge is its only
announcement channel. FAILED is handled explicitly -- an S3 trigger on the
transcript landing would have made failures silent.

WHY GetTranscriptionJob IS CALLED (THE EVENT IS "THIN")
--------------------------------------------------------
The live event carries only TranscriptionJobName and TranscriptionJobStatus
-- no transcript location, no source file, empty `resources`. So we call
back. Reverse-parsing the job name to find the file is NOT safe: the starter
sanitises and truncates stems, so the mapping is lossy. Compare Glue Data
Quality, whose event is "fat" and carries the score directly.

WHY TURNS ARE MERGED
--------------------
Transcribe splits one utterance into several segments on a pause. Real
example: the customer's "...I'd really like a refund if" / "possible." came
back as two segments. Scored separately, "possible." gets a sentiment of its
own. Consecutive same-speaker segments are merged into one turn first.

WHY SPEAKER LABELS ARE KEPT, WITH A LABELLED ROLE GUESS
--------------------------------------------------------
Transcribe returns spk_0 / spk_1. It does not know who is the agent. We keep
the labels Transcribe gave us and add `role_guess` with `role_method:
first_speaker` -- the person who speaks first on an inbound call is usually
the agent answering. That is an INFERENCE and is named as one; it is wrong
on outbound calls or when an IVR/customer speaks first. Same principle as
`textract_key_values`: record what the service said, label what we guessed.
Real contact-centre audio avoids the problem with one channel per party
(ChannelIdentification) or Amazon Transcribe Call Analytics.

WHY PER-TURN SENTIMENT, AND WHY BATCHED
---------------------------------------
Per-turn gives a TRAJECTORY: a customer negative in their first turn and
positive in their last is evidence the call resolved the issue -- a signal
a single whole-call score averages away. BatchDetectSentiment takes up to 25
texts per request, so a typical call is ONE API call rather than one per
turn.

BATCH APIs CAN PARTIALLY FAIL
-----------------------------
A failed item appears in ErrorList and is ABSENT from ResultList. Results
must be matched back by their `Index` field. Iterating ResultList in order
and zipping it against the turns attaches every sentiment after the first
failure to the wrong turn -- silently.
"""

import json
import os
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

import boto3

COMPREHEND_MAX_BYTES = 5000
BATCH_LIMIT = 25
OUTPUT_PREFIX = "processed-data/calls"
FAILED_PREFIX = "processed-data/calls/_failed"
CW_NAMESPACE = "CustomerFeedback/Calls"
HANDLED_STATUSES = {"COMPLETED", "FAILED"}


# ---------------------------------------------------------------- pure logic

def job_status(detail: dict):
    """Returns COMPLETED / FAILED, or None for anything we don't act on."""
    status = detail.get("TranscriptionJobStatus")
    return status if status in HANDLED_STATUSES else None


def parse_s3_uri(uri: str):
    """(bucket, key) from any of the three forms AWS hands back:
         s3://bucket/key                                    (MediaFileUri)
         https://s3.<region>.amazonaws.com/bucket/key       (path-style)
         https://bucket.s3.<region>.amazonaws.com/key       (virtual-hosted)
    GetTranscriptionJob returned path-style for TranscriptFileUri and s3://
    for MediaFileUri in the same response -- two formats, one call."""
    p = urlparse(uri)
    if p.scheme == "s3":
        return p.netloc, unquote(p.path.lstrip("/"))
    host = p.netloc
    path = unquote(p.path.lstrip("/"))
    if host.startswith("s3.") or host.startswith("s3-"):        # path-style
        bucket, _, key = path.partition("/")
        return bucket, key
    return host.split(".s3.")[0], path                          # virtual-hosted


def stem_of(key: str) -> str:
    return os.path.splitext(os.path.basename(key))[0]


def truncate_bytes(text: str, max_bytes: int = COMPREHEND_MAX_BYTES) -> str:
    enc = text.encode("utf-8")
    return text if len(enc) <= max_bytes else enc[:max_bytes].decode("utf-8", errors="ignore")


def merge_turns(audio_segments: list) -> list:
    """Merges consecutive segments from the same speaker into one turn."""
    turns = []
    for seg in audio_segments:
        speaker = seg.get("speaker_label", "unknown")
        text = seg.get("transcript", "").strip()
        if not text:
            continue
        if turns and turns[-1]["speaker"] == speaker:
            turns[-1]["text"] += " " + text
            turns[-1]["end_time"] = float(seg["end_time"])
        else:
            turns.append({
                "speaker": speaker,
                "start_time": float(seg["start_time"]),
                "end_time": float(seg["end_time"]),
                "text": text,
            })
    return turns


def infer_roles(turns: list) -> dict:
    """Labelled GUESS, not a fact: first speaker -> agent, second -> customer.

    Only valid for a 2-party inbound call. With 1 speaker (voicemail) or 3+
    (transfer, supervisor) position says nothing about role, so every speaker is
    'unknown' and role_method records why. Reliable roles need stereo audio +
    Transcribe Call Analytics ChannelDefinitions (AGENT/CUSTOMER per channel).
    """
    speakers = list(dict.fromkeys(t["speaker"] for t in turns))  # first-appearance order
    if len(speakers) != 2:
        method = f"not_inferred:speaker_count={len(speakers)}"
        return {s: {"role_guess": "unknown", "role_method": method} for s in speakers}
    agent, customer = speakers
    return {
        agent: {"role_guess": "agent", "role_method": "first_speaker"},
        customer: {"role_guess": "customer", "role_method": "first_speaker"},
    }


def pack_turns(texts: list, max_bytes: int = COMPREHEND_MAX_BYTES) -> list:
    """Greedy-pack whole turns into chunks of <= max_bytes UTF-8 bytes.

    Splits only BETWEEN turns, never inside one -- truncating a joined text would
    silently drop the END of the call. The one unavoidable cut: a single turn
    that alone exceeds the limit is byte-truncated and flagged truncated=True.
    """
    chunks = []
    for text in texts:
        if len(text.encode("utf-8")) > max_bytes:
            chunks.append({"text": truncate_bytes(text, max_bytes), "n_turns": 1, "truncated": True})
            continue
        last = chunks[-1] if chunks else None
        if last and not last["truncated"] and len((last["text"] + " " + text).encode("utf-8")) <= max_bytes:
            last["text"] += " " + text
            last["n_turns"] += 1
        else:
            chunks.append({"text": text, "n_turns": 1, "truncated": False})
    return chunks


def build_sentiment_docs(turns: list, roles: dict, max_bytes: int = COMPREHEND_MAX_BYTES) -> list:
    """Design B: one document set per speaker role, not one per turn.

    Measured on call_001 (see EXAM_NOTES 'Call sentiment -- per turn vs per
    speaker'): joining a speaker's turns gave Comprehend context (NEGATIVE 0.90
    vs 0.70 averaged per turn) at 40% fewer billed units (300-char minimum per doc).

    Comprehend has no speaker field -- each doc carries its slot here, and results
    are re-attached by Index. Unknown roles -> [] (nothing to attribute, send nothing).
    """
    def texts_for(role):
        return [t["text"] for t in turns if roles.get(t["speaker"], {}).get("role_guess") == role]

    customer, agent = texts_for("customer"), texts_for("agent")
    docs = []
    for slot, texts in (("customer_all", customer), ("agent_all", agent), ("customer_end", customer[-1:])):
        for part, chunk in enumerate(pack_turns(texts, max_bytes)):
            docs.append({"slot": slot, "part": part, **chunk})
    return docs


def batch_chunks(items: list, size: int = BATCH_LIMIT) -> list:
    return [items[i:i + size] for i in range(0, len(items), size)]


def apply_batch_results(items: list, offset: int, response: dict) -> list:
    """Attaches BatchDetectSentiment results to items (docs) BY INDEX, never by
    position in ResultList -- a partial failure leaves gaps. Returns per-item errors."""
    for r in response.get("ResultList", []):
        item = items[offset + r["Index"]]
        item["sentiment"] = r["Sentiment"]
        item["sentiment_scores"] = r["SentimentScore"]
    errors = []
    for e in response.get("ErrorList", []):
        item = items[offset + e["Index"]]
        item["sentiment"] = None
        item["sentiment_error"] = e.get("ErrorCode")
        errors.append({"index": offset + e["Index"], "error": e.get("ErrorCode")})
    return errors


SLOT_TO_SUMMARY = {
    "customer_all": "customer_overall",
    "agent_all": "agent_overall",
    "customer_end": "customer_end_state",
}
SCORE_KEYS = ("Positive", "Negative", "Neutral", "Mixed")


def call_sentiment_summary(docs: list) -> dict:
    """One result per slot. A slot split into several parts (>5,000 bytes) is
    combined by a BYTE-weighted mean of SentimentScore -- a long part counts more
    than a short one. Label = highest combined score."""
    out = {}
    for slot, name in SLOT_TO_SUMMARY.items():
        parts = [d for d in docs if d["slot"] == slot]
        scored = [d for d in parts if d.get("sentiment_scores")]
        if not parts:
            out[name] = {"sentiment": None, "reason": "no_text_for_role"}
            continue
        if not scored:
            out[name] = {"sentiment": None, "reason": "comprehend_error"}
            continue
        weights = [len(d["text"].encode("utf-8")) for d in scored]
        total = sum(weights)
        scores = {k: sum(d["sentiment_scores"][k] * w for d, w in zip(scored, weights)) / total
                  for k in SCORE_KEYS}
        out[name] = {
            "sentiment": max(scores, key=scores.get).upper(),
            "scores": scores,
            "method": "comprehend_speaker_concat",
            "parts": len(parts),
            "parts_scored": len(scored),
            "turns_used": sum(d["n_turns"] for d in scored),
            "truncated": any(d["truncated"] for d in parts),
        }
    return out


def build_processed_record(job: dict, transcript: dict, turns: list, roles: dict,
                           docs: list, errors: list) -> dict:
    _, audio_key = parse_s3_uri(job["Media"]["MediaFileUri"])
    _, transcript_key = parse_s3_uri(job["Transcript"]["TranscriptFileUri"])
    return {
        "job_name": job["TranscriptionJobName"],
        "status": "COMPLETED",
        "source_audio": audio_key,
        "transcript_key": transcript_key,
        "full_transcript": transcript["results"]["transcripts"][0]["transcript"],
        "speakers": roles,
        "turns": turns,
        "sentiment": call_sentiment_summary(docs),
        "sentiment_docs": [  # audit trail: what was sent, without repeating the text
            {k: d.get(k) for k in ("slot", "part", "n_turns", "truncated", "sentiment")}
            for d in docs
        ],
        "sentiment_errors": errors,
    }


def build_failure_record(job: dict) -> dict:
    _, audio_key = parse_s3_uri(job.get("Media", {}).get("MediaFileUri", "s3://unknown/unknown"))
    return {
        "job_name": job.get("TranscriptionJobName"),
        "status": "FAILED",
        "source_audio": audio_key,
        "failure_reason": job.get("FailureReason", "not provided"),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------- handler

def lambda_handler(event, context):
    transcribe = boto3.client("transcribe")
    s3 = boto3.client("s3")
    comprehend = boto3.client("comprehend")
    cloudwatch = boto3.client("cloudwatch")

    detail = event.get("detail", {})
    status = job_status(detail)
    if status is None:
        return {"statusCode": 200, "body": json.dumps(f"Ignored status {detail.get('TranscriptionJobStatus')}")}

    job = transcribe.get_transcription_job(
        TranscriptionJobName=detail["TranscriptionJobName"]
    )["TranscriptionJob"]
    bucket, _ = parse_s3_uri(job["Media"]["MediaFileUri"])

    # Every job, success or failure, counts toward a failure RATE.
    cloudwatch.put_metric_data(Namespace=CW_NAMESPACE, MetricData=[{
        "MetricName": "TranscriptionJobs", "Value": 1, "Unit": "Count",
        "Dimensions": [{"Name": "Status", "Value": status}],
    }])

    if status == "FAILED":
        record = build_failure_record(job)
        key = f"{FAILED_PREFIX}/{job['TranscriptionJobName']}.json"
        s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(record, indent=2),
                      ContentType="application/json")
        return {"statusCode": 200, "body": json.dumps({"failure_recorded": key})}

    t_bucket, t_key = parse_s3_uri(job["Transcript"]["TranscriptFileUri"])
    transcript = json.loads(s3.get_object(Bucket=t_bucket, Key=t_key)["Body"].read())

    turns = merge_turns(transcript["results"].get("audio_segments", []))

    roles = infer_roles(turns)
    docs = build_sentiment_docs(turns, roles)  # design B: per speaker, <=5,000 B each

    errors = []
    for i, chunk in enumerate(batch_chunks(docs)):  # <=25 docs per API call
        resp = comprehend.batch_detect_sentiment(
            TextList=[d["text"] for d in chunk], LanguageCode="en"
        )
        errors += apply_batch_results(docs, i * BATCH_LIMIT, resp)

    record = build_processed_record(job, transcript, turns, roles, docs, errors)
    _, audio_key = parse_s3_uri(job["Media"]["MediaFileUri"])
    out_key = f"{OUTPUT_PREFIX}/{stem_of(audio_key)}_processed.json"
    s3.put_object(Bucket=bucket, Key=out_key, Body=json.dumps(record, indent=2),
                  ContentType="application/json")
    return {"statusCode": 200, "body": json.dumps({"processed": out_key})}
