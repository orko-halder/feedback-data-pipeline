#!/usr/bin/env python3
"""Normalises the four processed record shapes into ONE feedback document.

WHY
Reviews, images, calls and surveys leave Part 2 as four incompatible JSON
shapes. No single prompt can reason across them, and cross-channel themes --
the actual deliverable -- only exist when one model sees all of them at once.

THE CONTRACT: every document separates three kinds of content, so the model
is never invited to mistake one for another.

    text        the customer's own words, verbatim
    signals     what we MEASURED deterministically (Comprehend, arithmetic)
    provenance  how far to trust this record, in plain words

Deliberately NOT carried across:
  - sentiment_docs, block_counts, row_number, parts_scored -- plumbing
  - turn timings -- the rendered dialogue keeps the meaning
  - entities / key_phrases -- extraction OF text the model reads in full
  - textract_key_values -- demonstrably unreliable (see README, "Textract FORMS
    key/values were audited and rejected").
    Unreliable STRUCTURE is worse than none: it invites the model to treat a
    phantom key as a fact. full_text plus a trust note is the honest handover.
"""

import json
import os
import re

SOURCE_REVIEW = "review"
SOURCE_IMAGE = "image"
SOURCE_CALL = "call"
SOURCE_SURVEY = "survey"


# Order references are the ONE deterministic cross-channel join in this data:
# image EAR-2200_CUST-2001 prints "Order #ORD-9001" and call_001's customer says
# "order nine thousand one", which Transcribe writes as "9001". Without this,
# a call can only be linked to a product by the model GUESSING from wording.
#
# Extracted BEFORE any PII redaction: an order number can itself be flagged as
# PII, and redacting it would destroy the join.
ORDER_PATTERNS = (
    re.compile(r"\bORD[- ]?(\d{3,})\b", re.I),  # "Order #ORD-9001"
    re.compile(r"\border\s*(?:#|no\.?|number)?\s*(\d{3,})\b", re.I),  # "order 9001"
)


def extract_order_ref(text: str):
    """Normalised to ORD-<digits>, or None. First match wins; the reference is
    a join key, so a second number in the text is not worth guessing between."""
    for pattern in ORDER_PATTERNS:
        m = pattern.search(text or "")
        if m:
            return f"ORD-{m.group(1)}"
    return None


def _doc(
    doc_id,
    source_type,
    source_key,
    text,
    signals,
    provenance,
    customer_id=None,
    product_id=None,
    occurred_on=None,
) -> dict:
    """Single constructor so every source produces IDENTICAL keys -- a missing
    key in one source type would make the batch prompt ragged."""
    return {
        "doc_id": doc_id,
        "source_type": source_type,
        "source_key": source_key,
        "customer_id": customer_id or None,
        "product_id": product_id or None,
        "occurred_on": occurred_on or None,
        # Derived from the text, not supplied by any source system.
        "order_ref": extract_order_ref(text),
        "text": (text or "").strip(),
        "signals": {
            "sentiment": signals.get("sentiment"),
            "sentiment_confidence": signals.get("sentiment_confidence"),
            "sentiment_source": signals.get("sentiment_source"),
            "rating": signals.get("rating"),
            "rating_satisfaction_gap": signals.get("rating_satisfaction_gap"),
        },
        "provenance": {
            "admissible": provenance.get("admissible", True),
            "truncated": provenance.get("truncated", False),
            "trust_notes": provenance.get("trust_notes", []),
        },
    }


def _stem(key: str) -> str:
    return os.path.splitext(os.path.basename(key))[0].replace("_processed", "")


def _top_score(scores: dict, label: str):
    """Comprehend's confidence for the label it chose. Score keys are
    Capitalised, labels are UPPERCASE -- hence the title()."""
    if not scores or not label:
        return None
    value = scores.get(label.title())
    return round(value, 4) if isinstance(value, (int, float)) else None


def from_review(record: dict, source_key: str) -> dict:
    meta = record.get("metadata", {})
    return _doc(
        doc_id=f"{SOURCE_REVIEW}:{_stem(source_key)}",
        source_type=SOURCE_REVIEW,
        source_key=source_key,
        text=record.get("original_text", ""),
        signals={
            "sentiment": record.get("sentiment"),
            "sentiment_confidence": _top_score(
                record.get("sentiment_scores", {}), record.get("sentiment")
            ),
            "sentiment_source": "comprehend",
            "rating": meta.get("rating"),
        },
        # The Comprehend processor only runs on records that PASSED validation
        # (it fails closed on a missing verdict), so reaching here means admissible.
        provenance={"admissible": True},
        customer_id=meta.get("customer_id"),
        product_id=meta.get("product_id"),
        occurred_on=meta.get("review_date"),
    )


def render_turns(turns: list, roles: dict) -> str:
    """Dialogue as 'agent: ...' lines. The model reads a conversation far
    better than a list of {speaker, start_time, end_time, text} objects, and
    timings change no judgement."""
    lines = []
    for t in turns:
        role = roles.get(t.get("speaker"), {}).get("role_guess", "unknown")
        lines.append(f"{role}: {t.get('text', '').strip()}")
    return "\n".join(lines)


def from_call(record: dict, source_key: str) -> dict:
    roles = record.get("speakers", {})
    customer = record.get("sentiment", {}).get("customer_overall", {})
    methods = {r.get("role_method") for r in roles.values()}
    notes = []
    if "first_speaker" in methods:
        notes.append(
            "speaker roles are a POSITIONAL GUESS (first speaker assumed to be the agent), "
            "not channel-verified -- treat attribution as uncertain"
        )
    if any(r.get("role_guess") == "unknown" for r in roles.values()):
        notes.append("speaker roles could not be inferred; attribution is unknown")
    truncated = any(
        v.get("truncated") for v in record.get("sentiment", {}).values() if isinstance(v, dict)
    )
    return _doc(
        doc_id=f"{SOURCE_CALL}:{_stem(record.get('source_audio', source_key))}",
        source_type=SOURCE_CALL,
        source_key=source_key,
        text=render_turns(record.get("turns", []), roles),
        signals={
            "sentiment": customer.get("sentiment"),
            "sentiment_confidence": _top_score(
                customer.get("scores", {}), customer.get("sentiment")
            ),
            # The CUSTOMER's sentiment only -- the agent's is scripted-positive
            # whatever happens, so averaging both would flatten every call.
            "sentiment_source": "comprehend/customer_turns",
        },
        provenance={
            "admissible": record.get("status") == "COMPLETED",
            "truncated": bool(truncated),
            "trust_notes": notes,
        },
    )


def from_survey_row(row: dict, source_key: str) -> dict:
    advisory = [i["check"] for i in row.get("issues", []) if i.get("severity") == "ADVISORY"]
    fatal = [i["check"] for i in row.get("issues", []) if i.get("severity") == "FATAL"]
    notes = []
    if fatal:
        notes.append(f"failed validation: {', '.join(fatal)}")
    if advisory:
        notes.append(f"incomplete fields: {', '.join(advisory)}")
    notes.append("ratings and the stated satisfaction label are self-reported")
    return _doc(
        doc_id=f"{SOURCE_SURVEY}:row_{row.get('row_number')}",
        source_type=SOURCE_SURVEY,
        source_key=source_key,
        # A survey's only free text is the comment. Everything else is a number
        # or a label and belongs in signals, not in prose the model must parse.
        text=row.get("comments") or "",
        signals={
            "sentiment": row.get("overall_satisfaction"),
            "sentiment_source": "self_reported_label",
            "rating": row.get("product_rating"),
            "rating_satisfaction_gap": row.get("rating_satisfaction_gap"),
        },
        provenance={"admissible": row.get("admissible", False), "trust_notes": notes},
        customer_id=row.get("customer_id"),
        product_id=row.get("product_id"),
        occurred_on=row.get("survey_date"),
    )


def from_image(record: dict, source_key: str) -> dict:
    meta = record.get("metadata", {})
    return _doc(
        doc_id=f"{SOURCE_IMAGE}:{_stem(record.get('image_key', source_key))}",
        source_type=SOURCE_IMAGE,
        source_key=source_key,
        text=record.get("full_text", ""),
        signals={},  # no sentiment was ever computed for images
        provenance={
            "admissible": True,
            "trust_notes": [
                "text is OCR output and may contain recognition errors",
                "no key/value structure is provided: Textract FORMS output was tested "
                "and found unreliable on these documents",
            ],
        },
        customer_id=meta.get("customer_id"),
        product_id=meta.get("product_id"),
    )


def normalise(record: dict, source_key: str) -> list:
    """Dispatch on the PREFIX of the S3 key, not on guessing from content.
    A survey file yields MANY documents; the others yield one."""
    if "/reviews/" in source_key:
        return [from_review(record, source_key)]
    if "/calls/" in source_key:
        return [from_call(record, source_key)]
    if "/images/" in source_key:
        return [from_image(record, source_key)]
    if "/surveys/" in source_key:
        return [from_survey_row(r, source_key) for r in record.get("records", [])]
    raise ValueError(f"unrecognised source prefix: {source_key}")


def admissible_only(docs: list) -> list:
    """The batch sent to the model. An inadmissible record is excluded from
    ANALYSIS but still counted in the manifest, so 'what was dropped' stays
    visible rather than silently shrinking the evidence base."""
    return [d for d in docs if d["provenance"]["admissible"] and d["text"]]


def manifest(docs: list, sent: list) -> dict:
    counts = {}
    for d in docs:
        counts[d["source_type"]] = counts.get(d["source_type"], 0) + 1
    return {
        "documents_total": len(docs),
        "documents_sent": len(sent),
        "excluded": len(docs) - len(sent),
        "by_source": counts,
        "approx_chars": sum(len(json.dumps(d)) for d in sent),
    }
