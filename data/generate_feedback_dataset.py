#!/usr/bin/env python3
"""Regenerates reviews, images and surveys around a WRITTEN-DOWN ground truth.

WHY THIS REPLACES generate_synthetic_data.py
The first generator drew review text from 8 templates and set `rating` and
`overall_satisfaction` with random.choice -- independent of the text. That
data cannot validate anything:
  - every product looked identical, so "themes" were template artefacts
  - every rating/sentiment mismatch was noise, so Part 4 had nothing real
    to detect and no way to be judged wrong

Here each product has an ISSUE PROFILE, text is written from that profile,
ratings follow the sentiment of the text, and the exceptions are PLANTED and
recorded in data/ground_truth.json. That file is what lets us SCORE the
model's report instead of admiring it.

THE CALLS ARE THE ANCHOR. call_001/002/003 already exist as audio (Polly +
ffmpeg, then transcribed) and are NOT regenerated -- re-running them would
mean new Polly spend and a new Transcribe job. The issue profiles below are
taken FROM those three call scripts, so the other channels corroborate a
complaint the calls already make:

    call_001 -> earbuds stopped charging after two weeks   (order 9001)
    call_002 -> robot vacuum gets stuck, sensor firmware
    call_003 -> blender extremely loud, smells of burning plastic

Deterministic: a fixed seed, so the dataset and its ground truth stay in
step. Re-running produces byte-identical files.

Usage:  python3 data/generate_feedback_dataset.py
"""

import csv
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
REVIEWS_DIR = os.path.join(RAW, "reviews")
IMAGES_DIR = os.path.join(RAW, "images")
SURVEYS_DIR = os.path.join(RAW, "surveys")
GROUND_TRUTH = os.path.join(HERE, "ground_truth.json")

SEED = 20260923

# --- The three issue profiles come from the CALL SCRIPTS; two controls ------
PRODUCTS = {
    "EAR-2200": {
        "name": "Wireless Earbuds",
        "issue": "stops charging within the first few weeks",
        "negative": [
            "Charged fine for the first fortnight and then the left bud stopped taking a charge completely.",
            "Three weeks in and the case no longer charges. Same fault my colleague had with hers.",
            "Battery died permanently after about two weeks of light use. Very disappointing for the price.",
            "The right earbud stopped charging overnight and never recovered. Support offered a refund.",
        ],
        "positive": ["Sound quality is genuinely excellent and they pair instantly with my phone."],
    },
    "VAC-3000": {
        "name": "Robot Vacuum",
        "issue": "gets stuck under furniture, sensor or firmware fault",
        "negative": [
            "Wedges itself under the sofa every single run and then powers off until I fish it out.",
            "The cliff sensors seem confused by dark rugs, it stops dead and reports an error.",
            "Needs a firmware update to stop it jamming under low furniture. Support confirmed a known issue.",
        ],
        "positive": [
            "Picks up pet hair far better than my old upright, and the app scheduling is handy."
        ],
    },
    "BLD-4500": {
        "name": "Smart Blender",
        "issue": "overheats, burning smell, excessive noise",
        "negative": [
            "Smells of hot plastic after about thirty seconds and the motor housing is too hot to touch.",
            "Extremely loud, and there is a burning smell whenever I blend anything thicker than soup.",
            "Motor cut out mid-smoothie and the whole unit reeked of burning. I have stopped using it.",
        ],
        "positive": ["Crushes ice properly and the jug is easy to clean, when it behaves."],
    },
    "FIT-1000": {
        "name": "Fitness Tracker",
        "issue": None,
        "negative": ["App occasionally needs a re-pair after a phone update, minor annoyance."],
        "positive": [
            "Step tracking is accurate against my treadmill and the battery lasts a full week.",
            "Comfortable enough to sleep in and the sleep stats actually match how I feel.",
            "Good value. Does what it says without any fuss.",
        ],
    },
    "SPK-1500": {
        "name": "Bluetooth Speaker",
        "issue": None,
        "negative": [],
        "positive": [
            "Far more bass than I expected from something this size, and it survived a rainy picnic.",
            "Pairs quickly, sounds great, and the battery easily lasts a weekend away.",
            "Solid little speaker. No complaints at all after three months.",
        ],
    },
}

# review_index -> (product, tone). 20 reviews, weighted to the three issues.
REVIEW_PLAN = (
    [("EAR-2200", "neg")] * 4
    + [("EAR-2200", "pos")]
    + [("VAC-3000", "neg")] * 3
    + [("VAC-3000", "pos")]
    + [("BLD-4500", "neg")] * 3
    + [("BLD-4500", "pos")]
    + [("FIT-1000", "pos")] * 3
    + [("FIT-1000", "neg")]
    + [("SPK-1500", "pos")] * 3
)

# Reviews whose RATING deliberately contradicts the text. Part 4 must find
# exactly these, and flagging anything else is a false positive.
PLANTED_MISMATCHES = {
    2: 5,  # scathing earbuds review, 5 stars
    9: 1,  # positive VACUUM review, 1 star
    16: 2,  # positive tracker review, 2 stars
}
# Malformed records -- the validation layer's work. Kept from v1, moved off
# the mismatch indices so the two experiments never collide.
MALFORMED = {
    3: "short_text",
    7: "no_product_id",
    11: "rating_out_of_range",
    14: "bad_date",
    18: "no_customer_id",
}

TONE_RATING = {"neg": (1, 2), "pos": (4, 5)}

COMPLAINT_TEMPLATES = {
    "EAR-2200": "RETURN REQUEST\nOrder #{order}\nProduct: {product} ({pid})\nReason: Stopped charging after\ntwo weeks. Left bud will not\nhold any charge.\nCustomer: {cust}",
    "VAC-3000": "RETURN REQUEST\nOrder #{order}\nProduct: {product} ({pid})\nReason: Unit becomes stuck under\nlow furniture and shuts down\nmid-clean.\nCustomer: {cust}",
    "BLD-4500": "RETURN REQUEST\nOrder #{order}\nProduct: {product} ({pid})\nReason: Strong burning smell and\nexcessive noise during use.\nCustomer: {cust}",
    "FIT-1000": "WARRANTY REGISTRATION\nOrder #{order}\nProduct: {product} ({pid})\nNote: Registering for the\nstandard two year warranty.\nCustomer: {cust}",
    "SPK-1500": "WARRANTY REGISTRATION\nOrder #{order}\nProduct: {product} ({pid})\nNote: Gift purchase, registering\nwarranty in my own name.\nCustomer: {cust}",
}

SATISFACTION_BY_MEAN = [  # (upper bound, label) -- label DERIVED from ratings
    (1.5, "Very Dissatisfied"),
    (2.5, "Dissatisfied"),
    (3.5, "Neutral"),
    (4.5, "Satisfied"),
    (5.1, "Very Satisfied"),
]

SURVEY_COMMENTS = {
    "EAR-2200": [
        "Charging case gave up after a fortnight.",
        "Had to return them, would not charge.",
        "Sound was great while they lasted, which was two weeks.",
    ],
    "VAC-3000": [
        "Keeps getting stuck under the sofa.",
        "Needs a firmware fix for the sensors.",
        "Stops mid-clean and I have to rescue it.",
    ],
    "BLD-4500": [
        "Burning smell after thirty seconds of use.",
        "Far too loud and it overheats.",
        "Motor cut out and smelled of burning.",
    ],
    "FIT-1000": ["Happy with it overall.", "Battery life is genuinely a week.", ""],
    "SPK-1500": ["Great sound for the size.", "No issues at all.", ""],
}
IMPROVEMENT_BY_PRODUCT = {
    "EAR-2200": "Battery life",
    "VAC-3000": "App reliability",
    "BLD-4500": "Build quality",
    "FIT-1000": "Packaging",
    "SPK-1500": "",
}


def satisfaction_for(product_rating: int, service_rating: int) -> str:
    mean = (product_rating + service_rating) / 2
    return next(label for bound, label in SATISFACTION_BY_MEAN if mean < bound)


def make_reviews(rng) -> list:
    os.makedirs(REVIEWS_DIR, exist_ok=True)
    truth = []
    for i, (pid, tone) in enumerate(REVIEW_PLAN, start=1):
        prod = PRODUCTS[pid]
        pool = prod[("negative" if tone == "neg" else "positive")] or prod["positive"]
        text = pool[(i - 1) % len(pool)]
        rating = rng.randint(*TONE_RATING[tone])
        planted = i in PLANTED_MISMATCHES
        if planted:
            rating = PLANTED_MISMATCHES[i]
        review = {
            "review_text": text,
            "product_id": pid,
            "customer_id": f"CUST-{1000 + i}",
            "rating": rating,
            "review_date": f"2026-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}",
        }

        defect = MALFORMED.get(i)
        if defect == "short_text":
            review["review_text"] = "Bad."
        elif defect == "no_product_id":
            del review["product_id"]
        elif defect == "rating_out_of_range":
            review["rating"] = 6
        elif defect == "bad_date":
            review["review_date"] = "15-09-2026"
        elif defect == "no_customer_id":
            del review["customer_id"]

        with open(os.path.join(REVIEWS_DIR, f"review_{i:03d}.json"), "w", encoding="utf-8") as f:
            json.dump(review, f, indent=2)
        truth.append(
            {
                "doc_id": f"review:review_{i:03d}",
                "product_id": pid,
                "tone": tone,
                "rating": review.get("rating"),
                "planted_mismatch": planted,
                "malformed": defect,
            }
        )
    print(f"Wrote {len(REVIEW_PLAN)} reviews to {REVIEWS_DIR}")
    return truth


def make_images() -> list:
    from PIL import Image, ImageDraw, ImageFont  # only needed here

    os.makedirs(IMAGES_DIR, exist_ok=True)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except Exception:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 22)
        except Exception:
            font = ImageFont.load_default()

    truth = []
    for i, (pid, prod) in enumerate(PRODUCTS.items(), start=1):
        cust, order = f"CUST-{2000 + i}", f"ORD-{9000 + i}"
        text = COMPLAINT_TEMPLATES[pid].format(
            product=prod["name"], pid=pid, cust=cust, order=order
        )
        img = Image.new("RGB", (640, 420), color=(250, 250, 245))
        d = ImageDraw.Draw(img)
        d.rectangle([10, 10, 630, 410], outline=(60, 60, 60), width=2)
        d.multiline_text((30, 40), text, fill=(20, 20, 20), font=font, spacing=10)
        img.save(os.path.join(IMAGES_DIR, f"{pid}_{cust}.png"))
        truth.append(
            {
                "doc_id": f"image:{pid}_{cust}",
                "product_id": pid,
                "order_ref": order,
                "kind": "return_request" if prod["issue"] else "warranty_registration",
            }
        )
    # ORD-9001 is the EAR-2200 return: call_001 says "order nine thousand one",
    # which Transcribe writes as "9001". That is the one CROSS-CHANNEL
    # deterministic join in the dataset -- everything else links by product.
    print(f"Wrote {len(PRODUCTS)} images to {IMAGES_DIR}")
    return truth


def make_surveys(rng, n=30) -> list:
    os.makedirs(SURVEYS_DIR, exist_ok=True)
    pids = list(PRODUCTS)
    plan = [pids[i % len(pids)] for i in range(n)]
    rng.shuffle(plan)
    planted = {8: "high_ratings_dissatisfied", 23: "low_ratings_very_satisfied"}

    rows, truth = [], []
    for i, pid in enumerate(plan, start=1):
        issue = PRODUCTS[pid]["issue"] is not None
        product_rating = rng.randint(1, 2) if issue else rng.randint(4, 5)
        service_rating = rng.randint(2, 5)
        label = satisfaction_for(product_rating, service_rating)

        kind = planted.get(i)
        if kind == "high_ratings_dissatisfied":
            product_rating, service_rating, label = 5, 5, "Dissatisfied"
        elif kind == "low_ratings_very_satisfied":
            product_rating, service_rating, label = 1, 1, "Very Satisfied"

        comments = SURVEY_COMMENTS[pid]
        row = {
            "customer_id": f"CUST-{3000 + i}",
            "product_id": pid,
            "survey_date": f"2026-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}",
            "product_rating": product_rating,
            "service_rating": service_rating,
            "overall_satisfaction": label,
            "improvement_area": IMPROVEMENT_BY_PRODUCT[pid],
            "comments": comments[(i - 1) % len(comments)],
        }

        malformed = None
        if i == 5:
            row["customer_id"] = ""
            malformed = "no_customer_id"
        if i == 21:
            row["survey_date"] = ""
            malformed = "no_survey_date"

        rows.append(row)
        truth.append(
            {
                "doc_id": f"survey:row_{i + 1}",
                "product_id": pid,
                "planted_mismatch": bool(kind),
                "malformed": malformed,
            }
        )

    out = os.path.join(SURVEYS_DIR, "surveys.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {n} survey rows to {out}")
    return truth


def main():
    rng = random.Random(SEED)
    reviews = make_reviews(rng)
    images = make_images()
    surveys = make_surveys(rng)

    truth = {
        "seed": SEED,
        "note": "Calls are NOT generated here; call_001/002/003 audio is unchanged "
        "and anchors the three issue profiles.",
        "products": {pid: {"name": p["name"], "issue": p["issue"]} for pid, p in PRODUCTS.items()},
        "expected_themes": [
            {
                "product_id": pid,
                "issue": p["issue"],
                "channels": ["review", "call", "image", "survey"],
                "review_docs": [
                    r["doc_id"] for r in reviews if r["product_id"] == pid and r["tone"] == "neg"
                ],
                "survey_rows": sum(1 for s in surveys if s["product_id"] == pid),
            }
            for pid, p in PRODUCTS.items()
            if p["issue"]
        ],
        "controls": [pid for pid, p in PRODUCTS.items() if not p["issue"]],
        "planted_rating_mismatches": (
            [r["doc_id"] for r in reviews if r["planted_mismatch"]]
            + [s["doc_id"] for s in surveys if s["planted_mismatch"]]
        ),
        "planted_malformed": (
            [{"doc_id": r["doc_id"], "defect": r["malformed"]} for r in reviews if r["malformed"]]
            + [{"doc_id": s["doc_id"], "defect": s["malformed"]} for s in surveys if s["malformed"]]
        ),
        "cross_channel_join": {
            "order_ref": "ORD-9001",
            "links": ["call:call_001", "image:EAR-2200_CUST-2001"],
        },
        "records": {"reviews": reviews, "images": images, "surveys": surveys},
    }
    with open(GROUND_TRUTH, "w", encoding="utf-8") as f:
        json.dump(truth, f, indent=2)
    print(f"Wrote ground truth to {GROUND_TRUTH}")
    print(f"  issue products: {[t['product_id'] for t in truth['expected_themes']]}")
    print(f"  controls:       {truth['controls']}")
    print(f"  planted mismatches: {truth['planted_rating_mismatches']}")


if __name__ == "__main__":
    main()
