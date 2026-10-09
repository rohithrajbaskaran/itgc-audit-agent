"""
Compare control test results against the answer key.

This is the ONLY script allowed to read data/answer_key/. The control tests
and agents never see it, so this is a genuine measure of accuracy.

Run from the project root (after control_tests.py):  python src/check_accuracy.py
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
found = pd.read_csv(ROOT / "results" / "exceptions.csv")
key = pd.read_csv(ROOT / "data" / "answer_key" / "answer_key.csv")

found_ids = set(zip(found.control_id, found.record_id))
key_ids = set(zip(key.control_id, key.record_id))

print(f"{'Control':<8} {'Planted':>8} {'Detected':>9} {'Missed':>7} {'False +':>8}")
for cid in sorted(key.control_id.unique()):
    k = {r for r in key_ids if r[0] == cid}
    f = {r for r in found_ids if r[0] == cid}
    print(f"{cid:<8} {len(k):>8} {len(k & f):>9} {len(k - f):>7} {len(f - k):>8}")

detected = len(key_ids & found_ids)
false_pos = len(found_ids - key_ids)
print(f"\nDetection rate: {detected}/{len(key_ids)} ({detected / len(key_ids):.0%})")
print(f"False positives: {false_pos}")

missed = key_ids - found_ids
if missed:
    print("\nMissed exceptions:")
    for cid, rid in sorted(missed):
        print(f"  {cid} {rid}")