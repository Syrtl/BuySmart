"""Explainable value chart scoring for product comparables."""

import hashlib
import math
import random
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ValueChartPoint(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    price: float
    rating: float
    review_count: int = Field(..., alias="reviewCount")
    quality: float
    quality_raw: float = Field(..., alias="qualityRaw")
    value_score: float = Field(..., alias="valueScore")


class ValueChartResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    product_id: str = Field(..., alias="productId")
    currency: str
    points: list[ValueChartPoint]
    optimal_id: str = Field(..., alias="optimalId")
    frontier_ids: list[str] = Field(..., alias="frontierIds")
    explanation: list[str]


def _safe_float(value: Any) -> float | None:
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
    out = []
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


def _safe_int(value: Any) -> int | None:
    parsed = _safe_float(value)
    if parsed is None:
        return None
    try:
        return int(round(parsed))
    except Exception:
        return None


def _seed_for_key(key: str) -> int:
    return int(hashlib.sha1(key.encode("utf-8", errors="replace")).hexdigest()[:16], 16)


def _synthetic_rating_reviews(item_id: str, title: str) -> tuple[float, int]:
    rng = random.Random(_seed_for_key(f"rr:{item_id}:{title}"))
    rating = round(3.4 + rng.random() * 1.5, 2)
    reviews = int(25 + (rng.random() ** 2) * 12000)
    return max(1.0, min(5.0, rating)), max(1, reviews)


def _resolve_rating_reviews(raw: dict[str, Any]) -> tuple[float, int]:
    item_id = str(raw.get("id") or "").strip()
    title = str(raw.get("title") or "").strip()

    rating = _safe_float(raw.get("rating"))
    if rating is None:
        rating, _ = _synthetic_rating_reviews(item_id, title)
    rating = max(1.0, min(5.0, float(rating)))

    reviews = _safe_int(raw.get("reviewCount"))
    if reviews is None:
        reviews = _safe_int(raw.get("reviews_count"))
    if reviews is None:
        reviews = _safe_int(raw.get("review_count"))
    if reviews is None:
        reviews = _safe_int(raw.get("reviews"))
    if reviews is None:
        _, reviews = _synthetic_rating_reviews(item_id, title)

    return rating, max(0, int(reviews))


def _normalize_points(raw_items: list[dict[str, Any]]) -> list[ValueChartPoint]:
    base_points: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for idx, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or "").strip()
        if not item_id:
            item_id = f"cmp-{idx + 1}"
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        title = str(raw.get("title") or "").strip() or f"Comparable {idx + 1}"
        price = _safe_float(raw.get("price"))
        if price is None or price <= 0:
            continue

        rating, review_count = _resolve_rating_reviews(raw)
        quality_raw = rating * math.log10(review_count + 1)
        base_points.append(
            {
                "id": item_id,
                "title": title,
                "price": round(float(price), 2),
                "rating": round(float(rating), 2),
                "review_count": int(review_count),
                "quality_raw": float(quality_raw),
            }
        )

    if not base_points:
        return []

    min_raw = min(point["quality_raw"] for point in base_points)
    max_raw = max(point["quality_raw"] for point in base_points)
    spread = max_raw - min_raw

    points: list[ValueChartPoint] = []
    for point in base_points:
        if spread <= 1e-12:
            quality = 50.0
        else:
            quality = 100.0 * (point["quality_raw"] - min_raw) / spread
        value_score = quality / max(point["price"], 1e-9)
        points.append(
            ValueChartPoint(
                id=point["id"],
                title=point["title"],
                price=point["price"],
                rating=point["rating"],
                reviewCount=point["review_count"],
                quality=round(float(quality), 2),
                qualityRaw=round(float(point["quality_raw"]), 5),
                valueScore=round(float(value_score), 6),
            )
        )

    return points


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(float(v) for v in values)
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return ordered[low]
    fraction = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def _pick_optimal(points: list[ValueChartPoint]) -> str:
    if not points:
        return ""

    candidates = [point for point in points if point.review_count >= 50]
    if len(candidates) < 8:
        candidates = list(points)

    if len(candidates) >= 10:
        p10 = _percentile([point.price for point in candidates], 10.0)
        p90 = _percentile([point.price for point in candidates], 90.0)
        if p90 <= 0:
            p90 = p10
        floor_price = p10 * 0.8
        filtered = [point for point in candidates if point.price >= floor_price]
        if filtered:
            candidates = filtered

    if not candidates:
        candidates = list(points)

    ranked = sorted(
        candidates,
        key=lambda point: (point.value_score, point.quality, -point.price, point.review_count),
        reverse=True,
    )
    return ranked[0].id


def _pareto_frontier_ids(points: list[ValueChartPoint]) -> list[str]:
    if not points:
        return []

    ordered = sorted(points, key=lambda point: (point.price, point.id))
    frontier_ids: list[str] = []
    best_quality = float("-inf")
    for point in ordered:
        if point.quality > best_quality + 1e-9:
            frontier_ids.append(point.id)
            best_quality = point.quality
    return frontier_ids


def _find_catalog_context(product_id: str, catalogs: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    normalized = str(product_id).strip().lower()
    for products in catalogs.values():
        for product in products:
            if str(product.get("id") or "").strip().lower() == normalized:
                return list(products), product
    return [], None


def _fallback_comparables(
    product_id: str,
    *,
    current_price: float | None,
    title: str | None,
    category: str | None,
) -> list[dict[str, Any]]:
    baseline = _safe_float(current_price)
    if baseline is None or baseline <= 0:
        baseline = 100.0

    seed = _seed_for_key(f"cmp:{product_id}:{baseline:.2f}")
    rng = random.Random(seed)
    count = 10 + int(rng.random() * 3)

    items: list[dict[str, Any]] = []
    for idx in range(count):
        if idx == 0:
            price = baseline
            item_id = str(product_id)
            item_title = str(title or f"{product_id} (current)").strip() or f"{product_id} (current)"
        else:
            drift = rng.uniform(-0.35, 0.35)
            price = baseline * (1.0 + drift)
            item_id = f"{product_id}-cmp-{idx}"
            item_title = f"Comparable Option {idx}"

        rating = round(3.3 + rng.random() * 1.6, 2)
        reviews = int(20 + (rng.random() ** 2) * 15000)
        items.append(
            {
                "id": item_id,
                "title": item_title,
                "category": category,
                "price": round(max(3.0, price), 2),
                "rating": max(1.0, min(5.0, rating)),
                "reviewCount": max(1, reviews),
            }
        )

    return items


def build_value_chart(
    *,
    product_id: str,
    catalogs: dict[str, list[dict[str, Any]]],
    current_price_hint: float | None = None,
    title_hint: str | None = None,
    category_hint: str | None = None,
    rating_hint: float | None = None,
    review_count_hint: int | None = None,
) -> ValueChartResponse:
    comparables, anchor = _find_catalog_context(product_id, catalogs)

    if comparables:
        raw_items = [dict(item) for item in comparables]
    else:
        raw_items = _fallback_comparables(
            product_id,
            current_price=current_price_hint,
            title=title_hint,
            category=category_hint,
        )

    if anchor is None:
        anchor = {
            "id": product_id,
            "title": title_hint or str(product_id),
            "price": current_price_hint,
            "category": category_hint,
            "rating": rating_hint,
            "reviewCount": review_count_hint,
        }

    normalized_anchor_id = str(product_id)
    has_anchor = any(str(item.get("id") or "").strip() == normalized_anchor_id for item in raw_items)
    if not has_anchor:
        anchor_price = _safe_float(anchor.get("price"))
        if anchor_price is not None and anchor_price > 0:
            raw_items.append(
                {
                    "id": product_id,
                    "title": anchor.get("title") or str(product_id),
                    "price": anchor_price,
                    "rating": _safe_float(anchor.get("rating")),
                    "reviewCount": _safe_int(anchor.get("reviewCount")),
                    "category": anchor.get("category") or category_hint,
                }
            )

    points = _normalize_points(raw_items)
    points = sorted(points, key=lambda point: (point.price, point.id))
    optimal_id = _pick_optimal(points)
    frontier_ids = _pareto_frontier_ids(points)

    explanation = [
        "Quality score = rating x log10(reviewCount + 1), then normalized to 0-100 within this comparable set.",
        "Best Value is the highest quality-per-dollar item among reliable candidates.",
        "Reliable candidates prefer reviewCount >= 50; if too few remain, the full set is used.",
        "Very low outlier prices are filtered using a percentile floor to reduce noisy wins.",
    ]

    return ValueChartResponse(
        productId=str(product_id),
        currency="USD",
        points=points,
        optimalId=optimal_id,
        frontierIds=frontier_ids,
        explanation=explanation,
    )
