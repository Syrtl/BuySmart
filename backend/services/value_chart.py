"""Explainable value chart scoring for product comparables."""

import hashlib
import json
import math
import os
import random
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_ALPHA_INTRINSIC = 0.65
_ALPHA_MARKET = 1.0 - _ALPHA_INTRINSIC


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
    quality_y: float
    intrinsic_q0: float
    market_qm: float
    breakdown: dict[str, Any]


class ValueChartResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    product_id: str = Field(..., alias="productId")
    currency: str
    points: list[ValueChartPoint]
    optimal_id: str = Field(..., alias="optimalId")
    frontier_ids: list[str] = Field(..., alias="frontierIds")
    explanation: list[str]


def _clamp01(value: float) -> float:
    try:
        numeric = float(value)
    except Exception:
        return 0.0
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


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


def _extract_q0_raw(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if 0.0 <= numeric <= 1.0:
        return _clamp01(numeric)
    if 0.0 <= numeric <= 100.0:
        return _clamp01(numeric / 100.0)
    return None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\\s*```$", "", raw).strip()

    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _prepare_llm_items(prepared: list[dict[str, Any]]) -> list[dict[str, Any]]:
    llm_items: list[dict[str, Any]] = []
    for item in prepared[:60]:
        llm_items.append(
            {
                "id": item["id"],
                "category": item.get("category") or "unknown",
                "title": item.get("title") or "",
                "brand": item.get("brand") or None,
                "description": str(item.get("description") or "")[:260],
                "specs": str(item.get("specs") or "")[:260],
                "price": item.get("price"),
            }
        )
    return llm_items


def _llm_intrinsic_scores(prepared: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    default_scores = {
        item["id"]: {"q0": 0.5, "reasons": [], "signals": {}}
        for item in prepared
    }
    if not prepared:
        return default_scores

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        return default_scores

    try:
        import openai
    except Exception:
        return default_scores

    llm_items = _prepare_llm_items(prepared)
    payload = json.dumps(llm_items, ensure_ascii=True)

    system_prompt = (
        "You evaluate intrinsic product quality across any category. "
        "Do NOT use review count, ratings, return rate, sentiment, popularity, or social proof. "
        "Use only intrinsic signals: materials/build, brand reliability, durability cues, specs completeness, "
        "product level/premium tier, and category-appropriate quality cues. "
        "Return STRICT JSON only: "
        "{\"items\":[{\"id\":\"...\",\"q0\":number,\"reasons\":[\"...\"],\"signals\":{...}}]}"
    )
    user_prompt = (
        "Score each item with q0 intrinsic quality.\\n"
        "Rules: q0 may be 0..1 or 0..100.\\n"
        "Keep reasons short (max 3).\\n"
        "Items:\\n" + payload
    )

    try:
        client = openai.OpenAI(api_key=api_key.strip())
        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-5"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=1800,
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception:
        return default_scores

    parsed = _extract_json_object(content)
    if not parsed:
        return default_scores

    raw_items = parsed.get("items")
    if not isinstance(raw_items, list):
        return default_scores

    for row in raw_items:
        if not isinstance(row, dict):
            continue
        row_id = str(row.get("id") or "").strip()
        if not row_id or row_id not in default_scores:
            continue

        q0 = _extract_q0_raw(row.get("q0"))
        if q0 is None:
            continue

        reasons = row.get("reasons")
        if not isinstance(reasons, list):
            reasons = []
        reasons = [str(x).strip() for x in reasons if str(x).strip()][:4]

        signals = row.get("signals")
        if not isinstance(signals, dict):
            signals = {}

        default_scores[row_id] = {
            "q0": q0,
            "reasons": reasons,
            "signals": signals,
        }

    return default_scores


def _resolve_rating_reviews(raw: dict[str, Any]) -> tuple[float | None, int | None]:
    rating = _safe_float(raw.get("rating"))
    if rating is not None:
        rating = max(1.0, min(5.0, float(rating)))

    reviews = _safe_int(raw.get("reviewCount"))
    if reviews is None:
        reviews = _safe_int(raw.get("reviews_count"))
    if reviews is None:
        reviews = _safe_int(raw.get("review_count"))
    if reviews is None:
        reviews = _safe_int(raw.get("reviews"))
    if reviews is not None:
        reviews = max(0, int(reviews))

    return rating, reviews


def _resolve_defect_rate(raw: dict[str, Any]) -> float | None:
    defect = _safe_float(raw.get("defect_rate"))
    if defect is None:
        defect = _safe_float(raw.get("return_rate"))
    if defect is None:
        defect = _safe_float(raw.get("returns_rate"))
    if defect is None:
        return None
    if defect > 1.0:
        defect = defect / 100.0
    return _clamp01(defect)


def _resolve_positive_share(raw: dict[str, Any], rating: float | None) -> float:
    positive = _safe_float(raw.get("positive_share"))
    if positive is None:
        positive = _safe_float(raw.get("positiveShare"))
    if positive is None:
        positive = _safe_float(raw.get("sentiment_positive"))
    if positive is not None:
        if positive > 1.0:
            positive = positive / 100.0
        return _clamp01(positive)

    if rating is not None:
        return _clamp01((float(rating) - 3.0) / 2.0)

    return 0.5


def _normalize_points(raw_items: list[dict[str, Any]]) -> list[ValueChartPoint]:
    prepared: list[dict[str, Any]] = []
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
        defect_rate = _resolve_defect_rate(raw)
        positive_share = _resolve_positive_share(raw, rating)

        if rating is None and review_count is None:
            synth_rating, synth_reviews = _synthetic_rating_reviews(item_id, title)
            rating = synth_rating
            review_count = synth_reviews

        prepared.append(
            {
                "id": item_id,
                "title": title,
                "price": round(float(price), 2),
                "category": str(raw.get("category") or "unknown").strip().lower() or "unknown",
                "brand": str(raw.get("brand") or "").strip() or None,
                "description": str(raw.get("description") or raw.get("snippet") or "").strip(),
                "specs": str(raw.get("specs") or raw.get("features") or "").strip(),
                "rating": rating,
                "review_count": 0 if review_count is None else int(max(0, review_count)),
                "defect_rate": defect_rate,
                "positive_share": positive_share,
            }
        )

    if not prepared:
        return []

    max_reviews_by_category: dict[str, int] = {}
    for item in prepared:
        category = item["category"]
        max_reviews_by_category[category] = max(max_reviews_by_category.get(category, 0), int(item["review_count"]))

    try:
        llm_scores = _llm_intrinsic_scores(prepared)
    except Exception:
        llm_scores = {
            item["id"]: {"q0": 0.5, "reasons": [], "signals": {}}
            for item in prepared
        }

    points: list[ValueChartPoint] = []
    for item in prepared:
        rating = item.get("rating")
        review_count = int(item.get("review_count") or 0)

        if rating is None:
            rn = 0.5
        else:
            rn = _clamp01(float(rating) / 5.0)

        max_review_count = int(max_reviews_by_category.get(item["category"], 0))
        if max_review_count > 0:
            nn = math.log1p(review_count) / math.log1p(max_review_count)
            nn = _clamp01(nn)
        else:
            nn = 0.0

        defect = item.get("defect_rate")
        if defect is None:
            defect = 0.1
        defect = _clamp01(float(defect))

        s = _clamp01(float(item.get("positive_share") if item.get("positive_share") is not None else 0.5))

        market_qm = (0.40 * rn) + (0.25 * nn) + (0.20 * (1.0 - defect)) + (0.15 * s)
        market_qm = _clamp01(market_qm)

        llm_payload = llm_scores.get(item["id"], {"q0": 0.5, "reasons": [], "signals": {}})
        q0 = _extract_q0_raw(llm_payload.get("q0"))
        if q0 is None:
            q0 = 0.5

        quality_y = _clamp01((_ALPHA_INTRINSIC * q0) + (_ALPHA_MARKET * market_qm))
        quality = quality_y * 100.0
        value_score = quality / max(float(item["price"]), 1e-9)

        rating_for_output = 0.0 if rating is None else float(rating)
        quality_raw = rating_for_output * math.log10(review_count + 1)

        reasons = llm_payload.get("reasons") if isinstance(llm_payload, dict) else []
        if not isinstance(reasons, list):
            reasons = []
        reasons = [str(x).strip() for x in reasons if str(x).strip()][:4]

        signals = llm_payload.get("signals") if isinstance(llm_payload, dict) else {}
        if not isinstance(signals, dict):
            signals = {}

        breakdown = {
            "Rn": round(rn, 5),
            "Nn": round(nn, 5),
            "D": round(defect, 5),
            "S": round(s, 5),
            "q0_reasons": reasons,
            "q0_signals": signals,
        }

        points.append(
            ValueChartPoint(
                id=item["id"],
                title=item["title"],
                price=float(item["price"]),
                rating=round(rating_for_output, 2),
                reviewCount=review_count,
                quality=round(quality, 2),
                qualityRaw=round(quality_raw, 5),
                valueScore=round(float(value_score), 6),
                quality_y=round(quality_y, 6),
                intrinsic_q0=round(q0, 6),
                market_qm=round(market_qm, 6),
                breakdown=breakdown,
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
        "Final quality_y = 0.65 x intrinsic_q0 (LLM) + 0.35 x market_qm.",
        "intrinsic_q0 is LLM-based product quality from category/title/brand/description/specs/price with safe fallback to 0.5.",
        "market_qm = 0.40*Rn + 0.25*Nn + 0.20*(1-D) + 0.15*S using ratings/review volume/defect and sentiment proxies.",
        "Best Value is the highest quality-per-dollar item among reliable candidates.",
    ]

    return ValueChartResponse(
        productId=str(product_id),
        currency="USD",
        points=points,
        optimalId=optimal_id,
        frontierIds=frontier_ids,
        explanation=explanation,
    )


# Test helper export
_normalize_q0_raw = _extract_q0_raw
