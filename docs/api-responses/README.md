# Raw API responses

Unmodified responses from the AWS APIs this pipeline calls, captured so
the real payload shape is on record rather than assumed.

Kept because of a real failure earlier in this project: an EventBridge
integration was coded against an *assumed* event shape and was wrong in
three separate ways (wrong `source`, wrong `detail-type`, and
`resultId` vs the documented `resultID`). Capturing the real payload
before writing code against it is cheaper than debugging it afterwards.

Sample input for the Comprehend files (from `review_001.json`):
> "The Wireless Earbuds exceeded my expectations, battery life is
> fantastic and setup took two minutes."

Sample input for the Textract files: `raw-data/images/EAR-2200_CUST-2001.png`

| File | API | Size | Notes |
|---|---|---|---|
| `comprehend_detect_sentiment.json` | `DetectSentiment` | ~230 B | One label + four confidence scores |
| `comprehend_detect_entities.json` | `DetectEntities` | ~215 B | Typed spans with offsets |
| `comprehend_detect_key_phrases.json` | `DetectKeyPhrases` | ~815 B | Noun phrases with offsets |
| `textract_detect_document_text.json` | `DetectDocumentText` | ~35 KB | PAGE/LINE/WORD blocks |
| `textract_analyze_document_forms.json` | `AnalyzeDocument` FORMS | ~47 KB | Above + KEY_VALUE_SET blocks |
| `transcribe_eventbridge_event.json` | EventBridge `Transcribe Job State Change` | ~380 B | THIN event: job name + status only |
| `transcribe_get_transcription_job.json` | `GetTranscriptionJob` | ~850 B | Where the transcript is; the call-back a thin event forces |
| `transcribe_transcript_output.json` | transcript file written to S3 | ~55 KB | Full text, per-word items, speaker-attributed segments |

## Why the Textract files are 150x larger than Comprehend's

Every block carries `Geometry` — a `BoundingBox` plus four `Polygon`
points, as floats. For 21 words that is 21 x ~10 floats before any text.
Comprehend returns offsets into the input string instead; character
positions are integers and the caller already has the text.

Trade-off: Textract's geometry is what lets you reconstruct layout,
highlight regions on the original image, or decide that two blocks are
on the same visual row. You pay for it in payload size.

## The truncation trap in the FORMS response

`KEY_VALUE_SET` for `Reason:` contains only `"Item arrived with a"` —
the value is cut at the line break, and the continuation
`"cracked casing near the power button."` appears ONLY in the LINE
blocks. No error, no confidence penalty. Structured extraction silently
drops multi-line values; the flat LINE blocks are the safety net.


## Transcribe: sample input

`raw-data/calls/call_001.mp3` — two Polly voices (Joanna as agent, Matthew
as customer), one mono track. Job `feedback-call_001-20260921T181552`,
completed in ~23 s.

## Transcribe: the event is THIN

The EventBridge event carries only `TranscriptionJobName` and
`TranscriptionJobStatus` — no transcript location, no source file, empty
`resources`. A consumer must call `GetTranscriptionJob` to learn where the
output went. Contrast Glue Data Quality, whose event is FAT (carries the
score). Reverse-parsing the job name is NOT a safe shortcut: the starter
sanitises and truncates stems, so the mapping is lossy.

## Transcribe: transcript structure

| Key | Contains | Use it for |
|---|---|---|
| `results.transcripts[0].transcript` | one flat string, no speakers | whole-call analysis |
| `results.items[]` | every word + punctuation, timestamps, confidence, `speaker_label` | fine-grained work |
| `results.speaker_labels.segments[]` | time ranges per speaker | alignment |
| `results.audio_segments[]` | speaker-attributed text turns | **per-speaker sentiment** |

Two things to handle downstream:
1. Labels are `spk_0` / `spk_1`, NOT agent / customer. Transcribe does not
   know roles. Mapping speaker -> role is the consumer's problem.
2. One utterance can span several segments: the customer's
   "...a refund if" / "possible." split on a pause. Merge consecutive
   same-speaker segments before analysing them.

Also note inverse text normalisation: the audio said "order nine thousand
one"; the transcript says "order 9001".
