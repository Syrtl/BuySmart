"""Build human-readable 'why' explanation for a recommendation."""


def build_why(
    query_text: str,
    product: dict,
    score: float,
    intent: dict,
) -> str:
    """
    Build a short explanation of why this product was recommended.
    intent may contain: budget, category, keywords. Marks over-budget when budget is set.
    """
    parts = []
    title = product.get("title", "Item")
    price = product.get("price")
    category = product.get("category", "")
    quality_score = product.get("quality_score")
    lifespan_years = product.get("lifespan_years")
    budget = intent.get("budget") if isinstance(intent, dict) else None
    if budget is not None and price is not None and price > budget:
        parts.append(f"Over budget by ${float(price - budget):.2f}.")

    if score > 0.7:
        parts.append("Strong match to your request.")
    elif score > 0.4:
        parts.append("Relevant to your request.")
    else:
        parts.append("Related option.")

    if price is not None:
        parts.append(f"Price ${price:.2f}.")
    if category:
        parts.append(f"Category: {category}.")
    if quality_score is not None and quality_score >= 0.8:
        parts.append("High quality rating.")
    if lifespan_years is not None and lifespan_years >= 5:
        parts.append(f"Long lifespan ({int(lifespan_years)} years).")

    return " ".join(parts) if parts else f"Recommended: {title}"
