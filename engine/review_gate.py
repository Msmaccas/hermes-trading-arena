"""
engine/review_gate.py — Post-generation quality check.
Checks analysis content for overly negative tone.
If entirely negative, the analysis file gets deleted.
"""

import os


# FIX 3 — Genuinely negative phrases only. No "pass" or "fail" (false positives).
_NEGATIVE_PHRASES = [
    "don't buy", "avoid this stock", "this stock is garbage",
    "not a buy", "stay away", "terrible stock",
    "completely avoid", "no redeeming qualities",
    "this is a loser", "skip this one", "not worth your time",
    "garbage", "no confidence",
]

_FINAL_NEGATIVE = [
    "avoid", "not a buy", "skip",
    "don't recommend", "no opportunity", "no setup",
]

_BUY_WATCH_WORDS = [
    "buy", "watch", "opportunity", "setup",
    "entry", "target", "potential", "strong",
]


def check_analysis(filepath):
    """
    Check an analysis file for overly negative content.
    Returns (passes: bool). If False, caller should delete the file.
    """
    if not os.path.isfile(filepath):
        return False

    with open(filepath) as f:
        content = f.read()

    lower = content.lower()
    prefix = lower[:2000]
    last_500 = lower[-500:]

    neg_count = sum(1 for p in _NEGATIVE_PHRASES if p in prefix)
    final_neg = sum(1 for p in _FINAL_NEGATIVE if p in last_500)
    has_actionable = any(w in prefix for w in _BUY_WATCH_WORDS)

    is_negative = (
        neg_count >= 3
        or (neg_count >= 2 and final_neg >= 2)
        or final_neg >= 3
        or (neg_count >= 2 and not has_actionable)
    )

    if is_negative:
        return False
    return True


def reject_negative_analysis(filepath):
    """
    Convenience: check and delete if negative.
    Returns True if file was kept, False if deleted.
    """
    if check_analysis(filepath):
        return True
    try:
        os.remove(filepath)
        print(f"[ReviewGate]  Deleted negative analysis: {os.path.basename(filepath)}", flush=True)
    except OSError:
        pass
    return False
