"""Intent parsing and product recommendation with optional embeddings."""

from __future__ import annotations

import os
import re
from typing import Any

_model = None


def _embeddings_disabled() -> bool:
    raw = os.environ.get("DISABLE_EMBEDDINGS", "0")
    return str(raw).strip().lower() in {"1", "true", "yes"}


def embeddings_enabled() -> bool:
    return not _embeddings_disabled()


def _get_model():
    global _model
    if _model is None:
        import torch  # noqa: F401
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _candidate_indices(
    products: list[dict],
    budget: float | None,
    category: str | None,
    k: int,
) -> list[int]:
    indices = list(range(len(products)))
    if category:
        indices = [i for i in indices if (products[i].get("category") or "").lower() == category.lower()]
    if not indices:
        indices = list(range(len(products)))

    if budget is not None and budget > 0:
        under_budget = [i for i in indices if (products[i].get("price") or 0) <= budget]
        over_budget = [i for i in indices if (products[i].get("price") or 0) > budget]
        if len(under_budget) >= k:
            indices = under_budget
        else:
            indices = under_budget + sorted(over_budget, key=lambda i: products[i].get("price") or 0)
    return indices


def _fast_score(query_tokens: set[str], product: dict, category: str | None) -> float:
    title = (product.get("title") or "").lower()
    desc = (product.get("description") or "").lower()
    cat = (product.get("category") or "").lower()
    text_tokens = _tokenize(f"{title} {desc} {cat}")
    overlap = len(query_tokens & text_tokens)
    base = overlap / max(1, len(query_tokens))
    score = 0.2 + (0.7 * base)
    if category and cat == category.lower():
        score += 0.15
    quality_score = product.get("quality_score")
    if isinstance(quality_score, (int, float)):
        score += min(0.1, float(quality_score) * 0.05)
    return max(0.0, min(1.0, float(score)))


def _recommend_fast(
    query_text: str,
    products: list[dict],
    indices: list[int],
    category: str | None,
    k: int,
) -> list[tuple[dict, float]]:
    query_tokens = _tokenize(query_text)
    scored = [(products[i], _fast_score(query_tokens, products[i], category)) for i in indices]
    scored.sort(
        key=lambda x: (
            -x[1],
            float(x[0].get("price") or 10**12),
            str(x[0].get("id") or ""),
            str(x[0].get("title") or ""),
        )
    )
    return scored[:k]


def _recommend_embeddings(
    query_text: str,
    products: list[dict],
    indices: list[int],
    k: int,
) -> list[tuple[dict, float]]:
    from sklearn.metrics.pairwise import cosine_similarity

    texts = []
    for p in products:
        title = p.get("title") or ""
        desc = p.get("description") or ""
        cat = p.get("category") or ""
        texts.append(f"{title}. {desc}. {cat}".strip() or "product")

    model = _get_model()
    query_emb = model.encode([query_text], normalize_embeddings=True)
    product_embs = model.encode(texts, normalize_embeddings=True)
    sims = cosine_similarity(query_emb, product_embs)[0]

    scored = [(products[i], float(sims[i])) for i in indices]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


def parse_intent(text: str) -> dict[str, Any]:
    """
    Parse user text into structured intent: budget, category, and raw keywords.
    """
    intent: dict[str, Any] = {"budget": None, "category": None, "keywords": []}
    if not text or not isinstance(text, str):
        return intent

    t = text.lower().strip()

    budget_match = re.search(
        r"(?:under|max|below|up to|budget)\s*\$?\s*(\d+(?:\.\d+)?)|^\$(\d+(?:\.\d+)?)",
        t,
        re.IGNORECASE,
    )
    if budget_match:
        g = budget_match.groups()
        value = float(g[0] or g[1] or 0)
        if value > 0:
            intent["budget"] = value

    category_map = {
        "chair": "furniture",
        "desk": "furniture",
        "furniture": "furniture",
        "keyboard": "electronics",
        "monitor": "electronics",
        "electronics": "electronics",
        "lamp": "furniture",
        "safety": "safety",
        "gloves": "safety",
        "helmet": "safety",
        "tools": "tools",
        "drill": "tools",
        "storage": "storage",
        "shelving": "storage",
    }
    for word, cat in category_map.items():
        if word in t:
            intent["category"] = cat
            break

    intent["keywords"] = [w for w in t.split() if len(w) > 2][:20]
    return intent


def _order_by_budget_then_score(
    scored: list[tuple[dict, float]],
    budget: float | None,
) -> list[tuple[dict, float]]:
    """Under-budget first, then over-budget by closeness to budget; deterministic tiebreak by score, id, title."""
    if budget is None or budget <= 0:
        scored.sort(
            key=lambda x: (
                -x[1],
                str(x[0].get("id") or ""),
                str(x[0].get("title") or ""),
            )
        )
        return scored
    under = [(p, s) for p, s in scored if (p.get("price") or 0) <= budget]
    over = [(p, s) for p, s in scored if (p.get("price") or 0) > budget]
    under.sort(key=lambda x: (-x[1], str(x[0].get("id") or ""), str(x[0].get("title") or "")))
    over.sort(
        key=lambda x: (
            (x[0].get("price") or 0) - budget,
            -x[1],
            str(x[0].get("id") or ""),
            str(x[0].get("title") or ""),
        )
    )
    return under + over


def recommend(
    query_text: str,
    products: list[dict],
    budget: float | None = None,
    category: str | None = None,
    k: int = 5,
) -> list[tuple[dict, float]]:
    """
    Recommend top-k products. Under-budget first, then over-budget by closeness to budget; deterministic.
    """
    if not products:
        return []

    intent = parse_intent(query_text)
    if budget is None:
        budget = intent.get("budget")
    if category is None:
        category = intent.get("category")

    indices = _candidate_indices(products, budget=budget, category=category, k=k)
    if _embeddings_disabled():
        scored = _recommend_fast(query_text, products, indices, category, k)
    else:
        try:
            scored = _recommend_embeddings(query_text, products, indices, k)
        except Exception:
            scored = _recommend_fast(query_text, products, indices, category, k)
    return _order_by_budget_then_score(scored, budget)
