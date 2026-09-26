"""
Extracts text and structured fields from product feedback images.

API CHOICE: AnalyzeDocument(FORMS) -- CHOSEN FOR EXPOSURE, NOT BECAUSE
IT IS THE RIGHT ENGINEERING CALL HERE. READ THIS BEFORE COPYING.
----------------------------------------------------------------------
For THIS data, DetectDocumentText is the correct choice and costs ~10x
less per page. An audit of what FORMS actually bought us:

  KEY_VALUE_SET field     used?  available elsewhere?
  ---------------------   -----  ---------------------------------
  Product  -> "... (VAC-3000)"   no   filename already gives VAC-3000
  Customer -> "CUST-2004"        no   filename already gives CUST-2004
  Order    -> "#ORD-9004"        no   regex ORD-\d+ over full_text
  Description / Reason / Issue   no   truncated (see below); full_text
                                      has the complete content
  phantom keys                   no   garbage (see below)

Nothing this module actually consumes comes from FORMS. `full_text`
is assembled from LINE blocks, which DetectDocumentText returns
identically, and the IDs come from the filename.

It is kept deliberately because this is exam-prep work and the FORMS
path demonstrates real, testable API surface that the cheap call never
exposes: KEY_VALUE_SET blocks, Textract's relationship-graph model, and
the multi-line failure mode documented below. That failure mode was only
discoverable BY calling the expensive API.

FORMS earns its cost when all three hold, and none hold here:
  1. the input is a genuine form, not prose with labels
  2. values are single-line by nature
  3. you have no filename / database / other source for the same IDs

TWO FAILURE MODES IN KEY_VALUE_SET, BOTH VERIFIED ON REAL OUTPUT
-----------------------------------------------------------------
Source image:
    Description: Left component
    stopped responding after 10 days.

Textract returns:
    "Description" -> "Left component"            <- value TRUNCATED at
                                                    the line break
    "stopped responding after" -> "10 days."     <- PHANTOM key invented
                                                    from the orphaned line

No error and no confidence penalty on either. The continuation has no
relationship back to its key, so it gets re-parsed as a pair of its own.

Consequence: this output CANNOT BE VALIDATED without knowing the document
type in advance. Measured across all five test images:

  - whitelisting label names   -- needs the labels known up front; useless
                                  for arbitrary customer-supplied images
  - confidence threshold       -- ranges OVERLAP. Real "Issue:" scored 60.0;
                                  phantom "manual is" scored 75.4. No cut
                                  separates them.
  - colon termination          -- high precision, poor recall: real "Order"
                                  carries no colon
  - geometry                   -- phantoms sit at the same Left as real
                                  labels on a left-aligned note

So we do not filter, and we do not claim a quality level we cannot
support. The output key is named for its PROVENANCE --
`textract_key_values` -- because where data came from is knowable even
when its correctness is not. "fields" would imply validation;
"fields_unverified" would imply a check was skipped rather than
impossible.
Content is taken from `full_text` and IDs from the filename; both are
trustworthy, neither comes from FORMS.

If a clean discrete `reason` field were ever needed, the fix is geometry:
a following LINE with near-identical `Left` and the next `Top` down is a
continuation and can be stitched back onto the value.

WHY THE FILENAME IS PARSED TOO
-------------------------------
Images are named <product_id>_<customer_id>.png -- a second, independent
source for IDs that Textract also reads off the page. Cheap, and useful
as a cross-check: if OCR and filename disagree, one of them is wrong.
In practice it is the STRONGER source, since it cannot be truncated.

WHY TEXTRACT AND NOT REKOGNITION
---------------------------------
Rekognition DetectText finds text in a SCENE (a sign, a shelf label) and
returns isolated detections. Textract models input as a DOCUMENT: typed
blocks, reading order, geometry, optional form/table structure. These
images are rendered notes, so they are documents.

NOTE ON full_text
------------------
`full_text` is NOT part of any Textract response -- it is assembled here
by joining LINE block texts. Textract returns blocks; reassembling a
readable document is the caller's job.
"""

import json
from urllib.parse import unquote_plus
import os
import re

import boto3

# <product_id>_<customer_id>.png  e.g. EAR-2200_CUST-2001.png
FILENAME_PATTERN = re.compile(r"^(?P<product_id>[A-Z]+-\d+)_(?P<customer_id>CUST-\d+)$")


def parse_metadata_from_key(key: str) -> dict:
    """Pure. Pulls IDs out of the object key. Returns empty strings rather
    than raising -- a badly named file should degrade, not crash the run."""
    stem = os.path.splitext(os.path.basename(key))[0]
    m = FILENAME_PATTERN.match(stem)
    if not m:
        return {"product_id": "", "customer_id": ""}
    return {"product_id": m.group("product_id"), "customer_id": m.group("customer_id")}


def extract_lines(blocks: list) -> list:
    """Pure. LINE blocks in the order Textract returned them, which is
    reading order for a single-column document."""
    return [b["Text"] for b in blocks if b["BlockType"] == "LINE" and "Text" in b]


def _text_of(block: dict, by_id: dict) -> str:
    """Resolves a block's text by walking its CHILD relationships.

    Textract output is a GRAPH, not a flat list: a LINE or KEY_VALUE_SET
    holds no text itself, only Ids of WORD blocks. Nothing can be read
    linearly -- every lookup goes through the id index.
    """
    words = []
    for rel in block.get("Relationships", []):
        if rel["Type"] != "CHILD":
            continue
        for cid in rel["Ids"]:
            child = by_id.get(cid, {})
            if child.get("BlockType") in ("WORD", "SELECTION_ELEMENT"):
                words.append(child.get("Text", ""))
    return " ".join(w for w in words if w)


def extract_key_values(blocks: list) -> dict:
    """Pure. Resolves KEY_VALUE_SET pairs into a plain dict.

    A KEY block carries two relationship types: CHILD (the words making up
    the key) and VALUE (the Id of the OTHER KEY_VALUE_SET block holding
    the value). Trailing colons are stripped so "Product:" keys as
    "Product".
    """
    by_id = {b["Id"]: b for b in blocks}
    pairs = {}
    for b in blocks:
        if b["BlockType"] != "KEY_VALUE_SET":
            continue
        if "KEY" not in b.get("EntityTypes", []):
            continue
        key = _text_of(b, by_id).rstrip(":").strip()
        value = ""
        for rel in b.get("Relationships", []):
            if rel["Type"] == "VALUE" and rel["Ids"]:
                value = _text_of(by_id.get(rel["Ids"][0], {}), by_id)
        if key:
            pairs[key] = value
    return pairs


def build_processed_record(key: str, blocks: list) -> dict:
    """Pure assembly, so the output shape is testable without calling AWS."""
    lines = extract_lines(blocks)
    return {
        "image_key": key,
        "full_text": "\n".join(lines),
        "lines": lines,
        # Named for PROVENANCE, not quality. "fields" would imply these
        # are validated labels; "fields_unverified" would imply a check
        # was merely skipped. Neither is true -- verification is
        # IMPOSSIBLE without knowing the document type (see docstring).
        # Provenance is the one thing statable with certainty: this is
        # Textract KEY_VALUE_SET output, judge it yourself.
        "textract_key_values": extract_key_values(blocks),
        "metadata": parse_metadata_from_key(key),
        "block_counts": {
            t: sum(1 for b in blocks if b["BlockType"] == t)
            for t in sorted({b["BlockType"] for b in blocks})
        },
    }


def lambda_handler(event, context):
    s3 = boto3.client("s3")
    textract = boto3.client("textract")

    bucket = event["Records"][0]["s3"]["bucket"]["name"]
    # S3 event notifications URL-encode the object key ("my file.json"
    # arrives as "my+file.json"). Used raw, the key 404s on get_object.
    key = unquote_plus(event["Records"][0]["s3"]["object"]["key"])

    if not key.lower().endswith((".png", ".jpg", ".jpeg")):
        return {"statusCode": 200, "body": json.dumps("Not an image file")}

    # S3Object form, not Bytes -- Textract reads straight from the bucket,
    # so the image never passes through this function's memory.
    response = textract.analyze_document(
        Document={"S3Object": {"Bucket": bucket, "Name": key}},
        FeatureTypes=["FORMS"],
    )

    processed = build_processed_record(key, response["Blocks"])

    processed_key = key.replace("raw-data/images", "processed-data/images")
    processed_key = os.path.splitext(processed_key)[0] + "_processed.json"

    s3.put_object(
        Bucket=bucket,
        Key=processed_key,
        Body=json.dumps(processed, indent=2),
        ContentType="application/json",
    )

    return {"statusCode": 200, "body": json.dumps({"processed": processed_key})}
