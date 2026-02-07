"""Shared quality-score helpers used by ranking and charts."""

from __future__ import annotations

import math
from typing import Any

ALPHA_INTRINSIC = 0.65
ALPHA_MARKET = 1.0 - ALPHA_INTRINSIC


def clamp01(value: float) -> float:
    try:
        n = float(value)
    except Exception:
        return 0.0
    if n < 0.0:
        return 0.0
    if n > 1.0:
        return 1.0
    return n


def safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip().replace(",", "")
    except Exception:
        return None
    if not text:
        return None

    out: list[str] = []
    dot_used = False
    for ch in text:
        if ch.isdigit():
            out.append(ch)
        elif ch == "." and not dot_used:
            out.append(ch)
            dot_used = True
        elif out:
            break
    if not out:
        return None
    try:
        return float("".join(out))
    except Exception:
        return None


def normalize_q0_raw(q0_raw: Any, fallback: float = 0.5) -> float:
    """Accept q0 in 0..1 or 0..100. Return clamped 0..1 with safe fallback."""
    if isinstance(q0_raw, bool):
        return clamp01(fallback)
    if isinstance(q0_raw, (int, float)):
        numeric = float(q0_raw)
        if 0.0 <= numeric <= 1.0:
            return clamp01(numeric)
        if 0.0 <= numeric <= 100.0:
            return clamp01(numeric / 100.0)
    return clamp01(fallback)


def compute_market_qm(
    *,
    rating_avg: float | None,
    review_count: int | None,
    max_review_count_in_category: int | None,
    defect_rate: float | None,
    positive_share: float | None,
) -> tuple[float, dict[str, float]]:
    """Compute market validation Qm using a stable 0..1 formula."""
    if rating_avg is None:
        rn = 0.5
    else:
        rn = clamp01(float(rating_avg) / 5.0)

    max_reviews = int(max(0, int(max_review_count_in_category or 0)))
    reviews = int(max(0, int(review_count or 0)))
    if max_reviews > 0:
        nn = clamp01(math.log1p(reviews) / math.log1p(max_reviews))
    else:
        nn = 0.0

    if defect_rate is None:
        d = 0.1
    else:
        d_raw = float(defect_rate)
        if d_raw > 1.0:
            d_raw = d_raw / 100.0
        d = clamp01(d_raw)

    if positive_share is None:
        if rating_avg is None:
            s = 0.5
        else:
            s = clamp01((float(rating_avg) - 3.0) / 2.0)
    else:
        s_raw = float(positive_share)
        if s_raw > 1.0:
            s_raw = s_raw / 100.0
        s = clamp01(s_raw)

    qm = (0.40 * rn) + (0.25 * nn) + (0.20 * (1.0 - d)) + (0.15 * s)
    qm = clamp01(qm)
    return qm, {"Rn": rn, "Nn": nn, "D": d, "S": s}


def compute_quality_y(q0_raw: Any, market_qm: float, *, alpha: float = ALPHA_INTRINSIC) -> tuple[float, float]:
    """Compute final intrinsic+market quality value in 0..1."""
    q0 = normalize_q0_raw(q0_raw, fallback=0.5)
    a = clamp01(alpha)
    y = clamp01((a * q0) + ((1.0 - a) * clamp01(market_qm)))
    return y, q0
