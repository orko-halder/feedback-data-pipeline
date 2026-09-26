"""
Transcribe STARTER -- starts an async transcription job, then exits.

WHY TWO LAMBDAS INSTEAD OF ONE
-------------------------------
Transcribe is asynchronous: StartTranscriptionJob returns immediately and
the job runs for a time proportional to the audio length. A single Lambda
that started the job and then polled for completion (the SkillBuilder
sample does `while True: sleep(5)`) pays for every idle second and dies
at Lambda's 15-minute ceiling on long calls. So this function only STARTS
the job. Completion is delivered by Transcribe's own EventBridge event
("Transcribe Job State Change") to a separate collector Lambda. Nothing
waits, nothing idles.

WHY EVENTBRIDGE AND NOT AN S3 TRIGGER ON THE TRANSCRIPT
-------------------------------------------------------
An S3 trigger on the transcript landing fires only on SUCCESS -- a failed
job writes nothing, so nothing fires and the failure is silent. The
EventBridge event is emitted for FAILED as well as COMPLETED.

PERMISSIONS NOTE
----------------
Transcribe reads the input audio and writes the transcript using the
permissions of the identity that CALLED StartTranscriptionJob -- this
function's role. So the role needs s3:GetObject on the audio and
s3:PutObject on the transcript prefix, even though this code never touches
either object itself. Same pattern as Textract's S3Object input.

SPEAKER LABELS vs CHANNEL IDENTIFICATION
-----------------------------------------
Our synthetic calls are ONE mono track with two voices, so we use speaker
diarization (ShowSpeakerLabels). Real contact-centre recordings are often
STEREO with agent and customer on separate channels -- there,
ChannelIdentification is more accurate than diarization, and Amazon
Transcribe Call Analytics is the purpose-built API for that case.

KNOWN LIMITATION: AT-LEAST-ONCE DELIVERY
----------------------------------------
S3 event notifications are delivered at-least-once. A duplicate delivery
starts a second job with a different timestamp -- wasted cost, no
corruption, since both write the same transcript key. The idempotent fix
is to derive the job name from the object's ETag instead of the clock, so
a duplicate collides with ConflictException; not done here because it
also blocks our re-upload-to-retest workflow for identical files.
"""

import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3

SUPPORTED_FORMATS = {"mp3", "mp4", "wav", "flac", "ogg", "amr", "webm", "m4a"}
TRANSCRIPT_PREFIX = "transcriptions"
# Transcribe job names allow only [0-9a-zA-Z._-], max 200 characters.
JOB_NAME_DISALLOWED = re.compile(r"[^0-9a-zA-Z._-]")
JOB_NAME_MAX = 200
JOB_NAME_PREFIX = "feedback-"
TIMESTAMP_LEN = len("-20260101T000000")


def _stem(key: str) -> str:
    return os.path.splitext(os.path.basename(key))[0]


def media_format_from_key(key: str):
    """Pure. Returns Transcribe's MediaFormat, or None if unsupported."""
    ext = os.path.splitext(key)[1].lower().lstrip(".")
    return ext if ext in SUPPORTED_FORMATS else None


def parse_s3_event(event: dict):
    """Pure. Extracts (bucket, key) from an S3 event notification, with the
    key URL-DECODED. S3 delivers keys encoded -- "my call.mp3" arrives as
    "my+call.mp3" -- and using it raw produces a MediaFileUri that 404s.
    Pulled out of the handler specifically so this fix can be unit tested."""
    record = event["Records"][0]["s3"]
    return record["bucket"]["name"], unquote_plus(record["object"]["key"])


def build_job_name(key: str, now: datetime) -> str:
    """Pure -- the clock is passed in, not read, so this is testable.
    Unique per run (timestamp) yet traceable back to the file (stem).
    The stem is truncated so the whole name stays within Transcribe's
    200-character limit rather than failing at the API."""
    safe_stem = JOB_NAME_DISALLOWED.sub("-", _stem(key))
    budget = JOB_NAME_MAX - len(JOB_NAME_PREFIX) - TIMESTAMP_LEN
    return f"{JOB_NAME_PREFIX}{safe_stem[:budget]}-{now.strftime('%Y%m%dT%H%M%S')}"


def derive_output_key(key: str) -> str:
    """raw-data/calls/call_001.mp3 -> transcriptions/call_001.json

    A prefix of its own. Nothing is triggered by transcriptions/, so the
    transcript landing cannot loop back into this function."""
    return f"{TRANSCRIPT_PREFIX}/{_stem(key)}.json"


def build_start_job_request(bucket: str, key: str, now: datetime):
    """Pure. The complete StartTranscriptionJob request, or None if the
    file is not a supported audio format."""
    fmt = media_format_from_key(key)
    if fmt is None:
        return None
    return {
        "TranscriptionJobName": build_job_name(key, now),
        "Media": {"MediaFileUri": f"s3://{bucket}/{key}"},
        "MediaFormat": fmt,
        "LanguageCode": "en-US",
        "OutputBucketName": bucket,
        "OutputKey": derive_output_key(key),
        "Settings": {"ShowSpeakerLabels": True, "MaxSpeakerLabels": 2},
    }


def lambda_handler(event, context):
    transcribe = boto3.client("transcribe")

    bucket, key = parse_s3_event(event)

    params = build_start_job_request(bucket, key, datetime.now(timezone.utc))
    if params is None:
        return {"statusCode": 200, "body": json.dumps(f"Skipped unsupported file: {key}")}

    transcribe.start_transcription_job(**params)

    return {"statusCode": 200, "body": json.dumps({"started": params["TranscriptionJobName"]})}
