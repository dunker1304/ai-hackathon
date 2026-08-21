"""Normalization eval: top-1 / top-3 accuracy + out-of-catalog handling.

Run from backend/:  uv run python scripts/eval_normalization.py
"""

from __future__ import annotations

import json
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal
from app.normalize import normalize_listing

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def main() -> None:
    cases = [
        json.loads(line)
        for line in (DATA_DIR / "eval_listings.jsonl").read_text().splitlines()
        if line.strip()
    ]
    db = SessionLocal()
    top1 = top3 = 0
    ooc_correct = ooc_total = 0
    false_matches: list[tuple[str, str | None, str | None]] = []

    try:
        for case in cases:
            result = normalize_listing(db, case["title"])
            got = result["product_type"].id if result["product_type"] else None
            expected = case["expected"]
            candidate_ids = [c["id"] for c in result["candidates"][:3]]

            if expected is None:
                ooc_total += 1
                if got is None:
                    ooc_correct += 1
                else:
                    false_matches.append((case["title"], expected, got))
            else:
                if got == expected:
                    top1 += 1
                elif expected in candidate_ids:
                    top3 += 1
                else:
                    false_matches.append((case["title"], expected, got))

    finally:
        db.close()

    in_catalog = len(cases) - ooc_total
    print(f"\n=== Normalization eval ({len(cases)} cases) ===")
    print(f"top-1 accuracy : {top1}/{in_catalog} = {top1 / in_catalog:.0%}")
    print(f"top-3 accuracy : {(top1 + top3)}/{in_catalog} = {(top1 + top3) / in_catalog:.0%}")
    print(f"OOC handled    : {ooc_correct}/{ooc_total} = {ooc_correct / max(ooc_total, 1):.0%}")
    if false_matches:
        print("\nMisses:")
        for title, expected, got in false_matches:
            print(f"  '{title[:70]}' expected={expected} got={got}")


if __name__ == "__main__":
    main()
