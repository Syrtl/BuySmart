"""
ProcureWise Assistant: use ONLY the provided catalog. Do not invent facts.
When data is missing, say "Unknown from catalog" and ask at most one follow-up question.
Return JSON in the required schema.
"""

from __future__ import annotations

import re
from typing import Any

from backend.services.tco import compute_tco

# Catalog field names we use (no external data).
CATALOG_FIELDS = {"id", "title", "description", "price", "category", "quality_score", "lifespan_years"}
# Fields that would improve confidence but are not in catalog (for "unknowns").
COMMON_MISSING_FIELDS = ["warranty", "exact_dimensions", "compatibility_list", "user_ratings_count"]


def parse_request(text: str) -> dict[str, Any]:
    """Parse user text into parsed_request: budget, category, must_haves, nice_to_haves."""
    out: dict[str, Any] = {
        "budget": None,
        "category": None,
        "must_haves": [],
        "nice_to_haves": [],
    }
    if not text or not isinstance(text, str):
        return out
    t = text.lower().strip()

    # Budget
    budget_match = re.search(
        r"(?:under|max|below|up to|budget)\s*\$?\s*(\d+(?:\.\d+)?)|^\$(\d+(?:\.\d+)?)",
        t,
        re.IGNORECASE,
    )
    if budget_match:
        g = budget_match.groups()
        val = float(g[0] or g[1] or 0)
        if val > 0:
            out["budget"] = val

    # Category
    category_map = {
        "chair": "furniture", "desk": "furniture", "furniture": "furniture",
        "keyboard": "electronics", "monitor": "electronics", "electronics": "electronics",
        "lamp": "furniture", "safety": "safety", "gloves": "safety", "helmet": "safety",
        "tools": "tools", "drill": "tools", "storage": "storage", "shelving": "storage",
        "lighting": "lighting", "electrical": "electrical", "hvac": "HVAC",
        "material-handling": "material-handling", "accessories": "accessories",
    }
    for word, cat in category_map.items():
        if word in t:
            out["category"] = cat
            break

    # Must-haves: strong constraint words; nice-to-haves: other meaningful words
    words = [w for w in re.split(r"\W+", t) if len(w) > 2]
    must_keywords = {"durable", "cheap", "budget", "ergonomic", "heavy", "industrial", "wireless", "led"}
    must_haves = [w for w in words if w in must_keywords or w.isdigit()]
    nice_to_haves = [w for w in words if w not in must_keywords and not w.isdigit()][:15]
    out["must_haves"] = list(dict.fromkeys(must_haves))[:10]
    out["nice_to_haves"] = list(dict.fromkeys(nice_to_haves))[:10]

    return out


def _relevance_score(product: dict, parsed: dict[str, Any]) -> float:
    """Score 0..1 from title/description/category match. Uses only catalog text."""
    score = 0.0
    title = (product.get("title") or "").lower()
    desc = (product.get("description") or "").lower()
    cat = (product.get("category") or "").lower()
    text = f"{title} {desc} {cat}"
    must = set((parsed.get("must_haves") or []))
    nice = set((parsed.get("nice_to_haves") or []))
    for w in must:
        if w in text:
            score += 0.3
    for w in nice:
        if w in text:
            score += 0.1
    if parsed.get("category") and cat == parsed["category"].lower():
        score += 0.4
    return min(1.0, score)


def _value_rank_term(product: dict) -> tuple[float, float]:
    """(value_score, -tco) for sorting: higher value, lower TCO preferred. Uses only catalog fields."""
    price = product.get("price")
    if price is None or price <= 0:
        price = 0.01
    q = product.get("quality_score")
    if q is None:
        q = 0.5
    lifespan = product.get("lifespan_years")
    if lifespan is None or lifespan <= 0:
        lifespan = 1.0
    value = q / price if price else 0
    tco = compute_tco(price, lifespan)
    return (value, -tco)


def _score_explanation_bullets(product: dict) -> list[str]:
    """Bullets using only catalog facts. For missing fields, state 'Unknown from catalog'."""
    bullets = []
    title = product.get("title")
    if title:
        bullets.append(f"Title (catalog): {title}.")
    else:
        bullets.append("Title: Unknown from catalog.")
    price = product.get("price")
    if price is not None:
        bullets.append(f"Price (catalog): ${float(price):.2f}.")
    else:
        bullets.append("Price: Unknown from catalog.")
    cat = product.get("category")
    if cat:
        bullets.append(f"Category (catalog): {cat}.")
    else:
        bullets.append("Category: Unknown from catalog.")
    q = product.get("quality_score")
    if q is not None:
        bullets.append(f"Quality score (catalog): {q}.")
    else:
        bullets.append("Quality score: Unknown from catalog.")
    lifespan = product.get("lifespan_years")
    if lifespan is not None:
        bullets.append(f"Lifespan (catalog): {lifespan} years.")
    else:
        bullets.append("Lifespan: Unknown from catalog.")
    desc = (product.get("description") or "").strip()
    if desc:
        bullets.append(f"Description (catalog): {desc[:120]}{'...' if len(desc) > 120 else ''}.")
    else:
        bullets.append("Description: Unknown from catalog.")
    return bullets


def _tco_block(product: dict) -> dict[str, Any]:
    """TCO object using only catalog: price, lifespan_years."""
    price = product.get("price")
    lifespan = product.get("lifespan_years")
    if lifespan is not None and lifespan > 0 and price is not None:
        yearly = compute_tco(price, lifespan)
        return {
            "available": True,
            "yearly_cost": round(yearly, 2),
            "formula": "price / lifespan_years",
            "notes": f"Catalog: price={price}, lifespan_years={lifespan}.",
        }
    return {
        "available": False,
        "yearly_cost": None,
        "formula": "null",
        "notes": "Unknown from catalog (lifespan_years missing or zero).",
    }


def _unknowns(product: dict) -> list[str]:
    """Missing catalog fields that would improve confidence."""
    missing = []
    if product.get("quality_score") is None:
        missing.append("quality_score")
    if product.get("lifespan_years") is None:
        missing.append("lifespan_years")
    for f in COMMON_MISSING_FIELDS:
        if f not in product or product.get(f) is None:
            missing.append(f)
    return missing[:5]


def recommend_from_catalog(
    user_text: str,
    products: list[dict],
    k: int = 3,
) -> dict[str, Any]:
    """
    Return best k items using only catalog data. Output matches ProcureWise Assistant JSON format.
    """
    parsed = parse_request(user_text)
    budget = parsed.get("budget")
    category = parsed.get("category")

    # Filter by budget and category (catalog-only)
    candidates = list(products)
    if budget is not None and budget > 0:
        candidates = [p for p in candidates if (p.get("price") or 0) <= budget]
    if not candidates:
        candidates = list(products)
    if category:
        cat_match = [p for p in candidates if (p.get("category") or "").lower() == category.lower()]
        if cat_match:
            candidates = cat_match

    # Rank: relevance first, then value (quality/price), then lower TCO
    def sort_key(p: dict) -> tuple[float, float, float]:
        rel = _relevance_score(p, parsed)
        val, neg_tco = _value_rank_term(p)
        return (-rel, -val, neg_tco)

    candidates.sort(key=sort_key)
    top = candidates[:k]

    recommendations = []
    for p in top:
        rec = {
            "id": str(p.get("id", "")),
            "title": str(p.get("title", "")),
            "price": float(p.get("price", 0)),
            "category": str(p.get("category", "")),
            "score_explanation": _score_explanation_bullets(p),
            "tco": _tco_block(p),
            "unknowns": _unknowns(p),
        }
        recommendations.append(rec)

    # At most one follow-up question when data is missing or constraints too tight.
    follow_up = None
    if not top and products:
        follow_up = "No catalog items match your constraints. Relax budget or category?"
    elif not parsed.get("budget") and user_text.strip():
        follow_up = "Do you have a budget (e.g. under $200)?"
    elif top and any(r["unknowns"] for r in recommendations):
        follow_up = "Some fields are unknown from catalog. Any other requirement (e.g. budget)?"

    return {
        "parsed_request": {
            "budget": parsed.get("budget"),
            "category": parsed.get("category"),
            "must_haves": parsed.get("must_haves", []),
            "nice_to_haves": parsed.get("nice_to_haves", []),
        },
        "recommendations": recommendations,
        "follow_up_question": follow_up,
    }
