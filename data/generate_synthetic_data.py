"""
Generates synthetic customer feedback data across all four modalities
for Task 1.3 (Data Validation and Processing Pipeline).

No real customer data anywhere -- everything here is fabricated, and
a handful of records are deliberately malformed so the validation
stages (Glue Data Quality, the text-validation Lambda) have real
problems to catch rather than a suspiciously perfect dataset.

Run from the project root:  python3 data/generate_synthetic_data.py
"""

import csv
import json
import os
import random
import subprocess
import sys

import boto3
from PIL import Image, ImageDraw, ImageFont

random.seed(42)

RAW = os.path.join(os.path.dirname(__file__), "raw")
REVIEWS_DIR = os.path.join(RAW, "reviews")
IMAGES_DIR = os.path.join(RAW, "images")
CALLS_DIR = os.path.join(RAW, "calls")
SURVEYS_DIR = os.path.join(RAW, "surveys")

PRODUCTS = [
    ("EAR-2200", "Wireless Earbuds"),
    ("BLD-4500", "Smart Blender"),
    ("FIT-1000", "Fitness Tracker"),
    ("VAC-3000", "Robot Vacuum"),
    ("SPK-1500", "Bluetooth Speaker"),
]

GOOD_SNIPPETS = [
    "The {product} exceeded my expectations, battery life is fantastic and setup took two minutes.",
    "Really happy with this {product}. Great value and it feels well built.",
    "The {product} works well most days but the app disconnects occasionally, still recommend it.",
    "Terrible experience, my {product} stopped working after a week and support was slow to respond.",
    "Good {product} overall, a bit noisy but does the job every single day.",
    "I love the {product}! Best purchase this year, would definitely buy again.",
    "The {product} arrived damaged. Packaging was poor and the item was scratched on delivery.",
    "Decent {product} for the price point, nothing extraordinary but reliable so far.",
]


def make_reviews(n=20):
    os.makedirs(REVIEWS_DIR, exist_ok=True)
    for i in range(1, n + 1):
        pid, pname = random.choice(PRODUCTS)
        review = {
            "review_text": random.choice(GOOD_SNIPPETS).format(product=pname),
            "product_id": pid,
            "customer_id": f"CUST-{1000 + i}",
            "rating": random.randint(1, 5),
            "review_date": f"2026-{random.randint(1, 9):02d}-{random.randint(1, 28):02d}",
        }

        # Deliberately inject data-quality problems into ~20% of records
        # so the validation layer has real work to do.
        if i == 3:
            review["review_text"] = "Bad."  # fails min-length check
        elif i == 7:
            del review["product_id"]  # fails completeness check
        elif i == 11:
            review["rating"] = 6  # out of 1-5 range
        elif i == 14:
            review["review_date"] = "15-09-2026"  # wrong format (not YYYY-MM-DD)
        elif i == 18:
            del review["customer_id"]  # fails completeness check

        with open(os.path.join(REVIEWS_DIR, f"review_{i:03d}.json"), "w") as f:
            json.dump(review, f, indent=2)
    print(f"Wrote {n} review files to {REVIEWS_DIR}")


COMPLAINT_TEMPLATES = [
    "RETURN REQUEST\nOrder #{order}\nProduct: {product} ({pid})\nReason: Item arrived with a\ncracked casing near the power button.\nCustomer: {cust}",
    "SERVICE NOTE\nProduct: {product} ({pid})\nIssue: Device will not power on\nafter firmware update v2.3.\nLogged by: {cust}",
    "WARRANTY CLAIM\nOrder #{order}\nProduct: {product} ({pid})\nComplaint: Battery drains fully\nwithin 2 hours of light use.\nCustomer: {cust}",
    "DAMAGE REPORT\nProduct: {product} ({pid})\nDescription: Left component\nstopped responding after 10 days.\nOrder #{order}  Customer: {cust}",
    "FEEDBACK CARD\nProduct: {product} ({pid})\nComment: Works fine but the\nmanual is missing pages 4-6.\nCustomer: {cust}",
]


def make_images(n=5):
    os.makedirs(IMAGES_DIR, exist_ok=True)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except Exception:
        font = ImageFont.load_default()

    for i in range(1, n + 1):
        pid, pname = PRODUCTS[(i - 1) % len(PRODUCTS)]
        cust = f"CUST-{2000 + i}"
        order = f"ORD-{9000 + i}"
        text = COMPLAINT_TEMPLATES[(i - 1) % len(COMPLAINT_TEMPLATES)].format(
            product=pname, pid=pid, cust=cust, order=order
        )

        img = Image.new("RGB", (640, 420), color=(250, 250, 245))
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, 630, 410], outline=(60, 60, 60), width=2)
        draw.multiline_text((30, 40), text, fill=(20, 20, 20), font=font, spacing=10)

        fname = f"{pid}_{cust}.png"
        img.save(os.path.join(IMAGES_DIR, fname))
    print(f"Wrote {n} images to {IMAGES_DIR}")


CALL_SCRIPTS = [
    [
        ("Joanna", "Thank you for calling customer support, this is Rachel speaking, how can I help you today?"),
        ("Matthew", "Hi, my wireless earbuds stopped charging after about two weeks of use."),
        ("Joanna", "I'm sorry to hear that. Can you confirm the order number so I can look into a replacement?"),
        ("Matthew", "Sure, it's order nine thousand one. I'd really like a refund if possible."),
        ("Joanna", "I understand, I have processed a full refund and you will see it within five business days."),
    ],
    [
        ("Joanna", "Hello, thanks for holding, this is support, how can I assist you?"),
        ("Matthew", "My robot vacuum keeps getting stuck under the sofa and shuts itself off."),
        ("Joanna", "Thanks for letting us know, that is a known issue with the sensor firmware."),
        ("Matthew", "Is there an update available for that?"),
        ("Joanna", "Yes, I can walk you through updating the firmware right now if you have a minute."),
    ],
    [
        ("Joanna", "Good afternoon, you are speaking with customer care, how can I help?"),
        ("Matthew", "The blender I bought is extremely loud and smells like burning plastic."),
        ("Joanna", "That does not sound right, I would recommend you stop using it immediately."),
        ("Matthew", "Okay, what do I do next?"),
        ("Joanna", "I am arranging a replacement unit and a prepaid return label for the current one."),
    ],
]


def make_calls(n=3):
    import shutil
    import tempfile

    os.makedirs(CALLS_DIR, exist_ok=True)
    polly = boto3.client("polly", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    # Build in a scratch dir OUTSIDE the synced project folder -- that
    # folder only allows writes, not deletes, so intermediate segment
    # files get cleaned up here and only the final mp3 is copied over.
    with tempfile.TemporaryDirectory() as scratch:
        for i, script in enumerate(CALL_SCRIPTS[:n], start=1):
            segment_files = []
            for j, (voice, line) in enumerate(script):
                resp = polly.synthesize_speech(Text=line, OutputFormat="mp3", VoiceId=voice)
                seg_path = os.path.join(scratch, f"seg_{i}_{j}.mp3")
                with open(seg_path, "wb") as f:
                    f.write(resp["AudioStream"].read())
                segment_files.append(seg_path)

            list_path = os.path.join(scratch, f"concat_{i}.txt")
            with open(list_path, "w") as f:
                for seg in segment_files:
                    f.write(f"file '{os.path.abspath(seg)}'\n")

            scratch_out = os.path.join(scratch, f"call_{i:03d}.mp3")
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", scratch_out],
                check=True, capture_output=True,
            )

            final_out = os.path.join(CALLS_DIR, f"call_{i:03d}.mp3")
            shutil.copy(scratch_out, final_out)
            print(f"Synthesized {final_out}")


def make_surveys(n=30):
    os.makedirs(SURVEYS_DIR, exist_ok=True)
    satisfaction_levels = ["Very Dissatisfied", "Dissatisfied", "Neutral", "Satisfied", "Very Satisfied"]
    improvement_areas = ["Battery life", "Build quality", "App reliability", "Customer support", "Packaging", ""]

    rows = []
    for i in range(1, n + 1):
        row = {
            "customer_id": f"CUST-{3000 + i}",
            "survey_date": f"2026-{random.randint(1, 9):02d}-{random.randint(1, 28):02d}",
            "product_rating": random.randint(1, 5),
            "service_rating": random.randint(1, 5),
            "overall_satisfaction": random.choice(satisfaction_levels),
            "improvement_area": random.choice(improvement_areas),
            "comments": random.choice([
                "Would like a longer warranty period.",
                "Great support experience overall.",
                "Setup instructions could be clearer.",
                "",
                "Delivery took longer than expected.",
            ]),
        }
        # A couple of intentionally missing key fields, matching real
        # survey-export messiness (skipped questions, dropped rows).
        if i == 5:
            row["customer_id"] = ""
        if i == 21:
            row["survey_date"] = ""
        rows.append(row)

    out_path = os.path.join(SURVEYS_DIR, "surveys.csv")
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {n} survey rows to {out_path}")


if __name__ == "__main__":
    make_reviews()
    make_images()
    make_surveys()
    if "--with-audio" in sys.argv:
        make_calls()
    else:
        print("Skipping Polly audio synthesis (pass --with-audio to include it).")
