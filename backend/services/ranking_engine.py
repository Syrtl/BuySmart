"""Deterministic ranking engine for assistant recommendations."""

from __future__ import annotations

import re
from typing import Any

from backend.services.quality_score import compute_market_qm, compute_quality_y, safe_float

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "if", "in", "into", "is", "it",
    "of", "on", "or", "that", "the", "to", "up", "with", "you", "your", "i", "me", "my", "we", "our",
    "want", "need", "looking", "find", "show", "please", "around", "about", "roughly", "budget", "price",
    "хочу", "нужно", "найди", "покажи", "примерно", "цена", "бюджет", "для", "под", "до", "от", "и", "в", "на",
}

_MATERIAL_KEYWORDS = {
    "steel", "stainless", "aluminum", "alloy", "iron", "brass", "copper",
    "wood", "oak", "walnut", "pine", "bamboo", "plywood",
    "plastic", "polycarbonate", "abs", "silicone", "rubber",
    "cotton", "linen", "wool", "polyester", "nylon", "leather", "suede",
    "glass", "ceramic", "porcelain",
    "сталь", "алюминий", "дерево", "дуб", "бамбук", "пластик", "силикон", "резина", "хлопок", "лен", "шерсть", "кожа", "стекло", "керамика",
}


def _clamp01(value: float) -> float:
    try:
        n = float(value)
    except Exception:
        return 0.0
    if n < 0.0:
        return 0.0
    if n > 1.0:
        return 1.0
    return n


def _safe_int(value: Any) -> int | None:
    parsed = safe_float(value)
    if parsed is None:
        return None
    try:
        return int(round(parsed))
    except Exception:
        return None


def _safe_price(value: Any) -> float | None:
    parsed = safe_float(value)
    if parsed is None:
        return None
    if parsed <= 0:
        return None
    return float(parsed)


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Zа-яА-Я0-9\-]+", str(text or "").lower())
    out: list[str] = []
    for token in tokens:
        if len(token) < 2:
            continue
        if token in _STOPWORDS:
            continue
        if token.isdigit():
            continue
        out.append(token)
    return out


def parse_intent_payload(user_text: str) -> dict[str, Any]:
    text = str(user_text or "").strip()
    lower = text.lower()

    intent: dict[str, Any] = {
        "budget": None,
        "min_price": None,
        "max_price": None,
        "target_price": None,
        "requirements": [],
        "materials": [],
    }

    m_range = re.search(
        r"(?:from|between|от)\s*\$?\s*(\d+(?:\.\d+)?)\s*(?:to|and|до|-)\s*\$?\s*(\d+(?:\.\d+)?)",
        lower,
        re.IGNORECASE,
    )
    if m_range:
        a = float(m_range.group(1))
        b = float(m_range.group(2))
        lo, hi = (a, b) if a <= b else (b, a)
        intent["min_price"] = lo
        intent["max_price"] = hi
        intent["budget"] = hi
        intent["target_price"] = (lo + hi) / 2.0

    if intent["budget"] is None:
        m_budget = re.search(
            r"(?:under|max|below|up to|budget|lower than|less than|до|не дороже|ниже)\s*\$?\s*(\d+(?:\.\d+)?)",
            lower,
            re.IGNORECASE,
        )
        if m_budget:
            value = float(m_budget.group(1))
            intent["budget"] = value
            intent["max_price"] = value
            intent["target_price"] = value

    if intent["target_price"] is None:
        m_around = re.search(
            r"(?:around|about|roughly|approximately|approx|примерно|около)\s*\$?\s*(\d+(?:\.\d+)?)",
            lower,
            re.IGNORECASE,
        )
        if m_around:
            value = float(m_around.group(1))
            intent["target_price"] = value
            intent["min_price"] = value * 0.8
            intent["max_price"] = value * 1.2
            intent["budget"] = intent["max_price"]

    req_tokens = _tokenize(lower)
    materials = [t for t in req_tokens if t in _MATERIAL_KEYWORDS]
    req_clean = [t for t in req_tokens if t not in _MATERIAL_KEYWORDS]

    intent["materials"] = list(dict.fromkeys(materials))[:8]
    intent["requirements"] = list(dict.fromkeys(req_clean))[:24]
    return intent


def _text_blob(product: dict[str, Any]) -> str:
    return " ".join(
        [
            str(product.get("title") or ""),
            str(product.get("description") or product.get("snippet") or ""),
            str(product.get("category") or ""),
            str(product.get("brand") or ""),
            str(product.get("specs") or ""),
        ]
    ).lower()


def _price_fit_score(price: float | None, intent: dict[str, Any]) -> tuple[float, list[str]]:
    flags: list[str] = []
    if price is None:
        flags.append("missing_price")
        return 25.0, flags

    min_price = intent.get("min_price")
    max_price = intent.get("max_price")
    budget = intent.get("budget")
    target = intent.get("target_price")

    if min_price is not None and max_price is not None:
        lo = float(min_price)
        hi = float(max_price)
        if lo <= price <= hi:
            return 100.0, flags
        if price < lo:
            gap = (lo - price) / max(lo, 1.0)
            return max(40.0, 90.0 - (gap * 70.0)), flags
        gap = (price - hi) / max(hi, 1.0)
        flags.append("over_budget")
        return max(0.0, 80.0 - (gap * 140.0)), flags

    if budget is not None:
        b = float(budget)
        if price <= b:
            savings = (b - price) / max(b, 1.0)
            return max(80.0, 100.0 - (savings * 22.0)), flags
        over = (price - b) / max(b, 1.0)
        flags.append("over_budget")
        return max(0.0, 72.0 - (over * 140.0)), flags

    if target is not None:
        t = float(target)
        diff = abs(price - t) / max(t, 1.0)
        return max(0.0, 100.0 - (diff * 120.0)), flags

    return 60.0, flags


def _requirement_match_score(product: dict[str, Any], intent: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    requirements = intent.get("requirements") or []
    if not requirements:
        return 60.0, [], []

    text = _text_blob(product)
    matched: list[str] = []
    missed: list[str] = []
    for token in requirements:
        if token in text:
            matched.append(token)
        else:
            missed.append(token)

    coverage = float(len(matched)) / float(max(1, len(requirements)))
    return round(coverage * 100.0, 2), matched[:10], missed[:10]


def _material_score(product: dict[str, Any], intent: dict[str, Any]) -> tuple[float, list[str]]:
    wanted = intent.get("materials") or []
    if not wanted:
        return 55.0, []

    text = _text_blob(product)
    matched = [token for token in wanted if token in text]
    if not matched:
        return 20.0, []

    ratio = float(len(matched)) / float(max(1, len(wanted)))
    return round(30.0 + (ratio * 70.0), 2), matched[:8]


def _quality_score(
    product: dict[str, Any],
    *,
    max_reviews_in_category: int,
) -> tuple[float, dict[str, float]]:
    rating = safe_float(product.get("rating"))
    if rating is not None:
        rating = max(0.0, min(5.0, float(rating)))

    reviews = _safe_int(product.get("reviewCount"))
    if reviews is None:
        reviews = _safe_int(product.get("reviews_count"))
    if reviews is None:
        reviews = _safe_int(product.get("review_count"))
    if reviews is None:
        reviews = _safe_int(product.get("reviews"))

    defect_rate = safe_float(product.get("defect_rate"))
    if defect_rate is None:
        defect_rate = safe_float(product.get("return_rate"))

    positive_share = safe_float(product.get("positive_share"))
    if positive_share is None:
        positive_share = safe_float(product.get("positiveShare"))

    market_qm, components = compute_market_qm(
        rating_avg=rating,
        review_count=reviews,
        max_review_count_in_category=max_reviews_in_category,
        defect_rate=defect_rate,
        positive_share=positive_share,
    )

    intrinsic_hint = product.get("intrinsic_q0")
    if intrinsic_hint is None:
        intrinsic_hint = product.get("quality_score")
        if isinstance(intrinsic_hint, (int, float)):
            # quality_score is often 0..1 in this project.
            intrinsic_hint = float(intrinsic_hint)

    quality_y, q0 = compute_quality_y(intrinsic_hint, market_qm)
    out = {
        "Rn": round(components["Rn"], 6),
        "Nn": round(components["Nn"], 6),
        "D": round(components["D"], 6),
        "S": round(components["S"], 6),
        "q0": round(q0, 6),
        "qm": round(market_qm, 6),
        "quality_y": round(quality_y, 6),
    }
    return round(quality_y * 100.0, 2), out


def rank_products(
    *,
    user_text: str,
    products: list[dict[str, Any]],
    k: int = 5,
    intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parsed_intent = intent if isinstance(intent, dict) else parse_intent_payload(user_text)

    category_reviews_max: dict[str, int] = {}
    for item in products:
        category = str(item.get("category") or "unknown").strip().lower() or "unknown"
        rv = _safe_int(item.get("reviewCount"))
        if rv is None:
            rv = _safe_int(item.get("reviews_count"))
        rv = max(0, int(rv or 0))
        category_reviews_max[category] = max(category_reviews_max.get(category, 0), rv)

    scored_rows: list[dict[str, Any]] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        product_id = str(product.get("id") or "").strip()
        title = str(product.get("title") or "").strip()
        if not title:
            continue

        category = str(product.get("category") or "unknown").strip().lower() or "unknown"
        max_reviews_in_category = int(category_reviews_max.get(category, 0))

        quality_score, quality_components = _quality_score(
            product,
            max_reviews_in_category=max_reviews_in_category,
        )
        price = _safe_price(product.get("price"))
        price_fit_score, price_flags = _price_fit_score(price, parsed_intent)
        requirement_match, matched_terms, missed_terms = _requirement_match_score(product, parsed_intent)
        material_score, matched_materials = _material_score(product, parsed_intent)

        total_score = (
            (0.45 * quality_score)
            + (0.35 * price_fit_score)
            + (0.15 * requirement_match)
            + (0.05 * material_score)
        )

        flags = list(price_flags)
        if price is None:
            flags.append("no_price")
        if missed_terms and len(missed_terms) > len(matched_terms):
            flags.append("low_requirement_coverage")

        breakdown = {
            "id": product_id,
            "qualityScore": round(quality_score, 2),
            "priceFitScore": round(price_fit_score, 2),
            "requirementMatch": round(requirement_match, 2),
            "materialScore": round(material_score, 2),
            "totalScore": round(total_score, 2),
            "flags": flags,
            "qualityComponents": quality_components,
            "matchedTerms": matched_terms,
            "matchedMaterials": matched_materials,
        }

        scored_rows.append(
            {
                "id": product_id,
                "title": title,
                "price": price,
                "category": category,
                "score": round(total_score / 100.0, 6),
                "breakdown": breakdown,
                "_sort": (
                    -round(total_score, 8),
                    1 if price is None else 0,
                    float(price or 0.0),
                    product_id,
                    title,
                ),
                "product": product,
            }
        )

    scored_rows.sort(key=lambda row: row["_sort"])

    # Deduplicate by id/title while preserving rank.
    seen_keys: set[str] = set()
    ranked: list[dict[str, Any]] = []
    for row in scored_rows:
        key = row["id"] or ("title:" + row["title"].lower())
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ranked.append(row)

    limit = max(3, min(5, int(k or 5)))
    top_rows = ranked[:limit]

    recommendations: list[dict[str, Any]] = []
    for row in top_rows:
        breakdown = dict(row["breakdown"])
        score_lines = [
            f"Quality score: {breakdown['qualityScore']}/100.",
            f"Price fit: {breakdown['priceFitScore']}/100.",
            f"Requirement match: {breakdown['requirementMatch']}/100.",
            f"Material score: {breakdown['materialScore']}/100.",
        ]
        if "over_budget" in breakdown.get("flags", []):
            score_lines.append("Above your budget target.")

        recommendations.append(
            {
                "id": row["id"],
                "title": row["title"],
                "price": row["price"],
                "url": row["product"].get("url"),
                "category": row["category"],
                "score_explanation": score_lines,
                "tco": {
                    "available": False,
                    "yearly_cost": None,
                    "formula": "null",
                    "notes": "Unknown from catalog.",
                },
                "unknowns": [],
                "qualityScore": breakdown["qualityScore"],
                "priceFitScore": breakdown["priceFitScore"],
                "requirementMatch": breakdown["requirementMatch"],
                "materialScore": breakdown["materialScore"],
                "totalScore": breakdown["totalScore"],
                "flags": breakdown["flags"],
                "breakdown": breakdown,
            }
        )

    return {
        "intent": parsed_intent,
        "ranked": ranked,
        "recommendations": recommendations,
    }
